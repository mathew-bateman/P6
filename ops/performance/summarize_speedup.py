"""Join fresh before/after timings and complete all-project acceptance evidence.

No database is queried. Every gate fails closed for missing or incomplete
receipts. Nested calculation stages explain time; they are never added to the
whole-job stopwatch or to their parent stage.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import statistics
import sys

from summarize_all_projects import (
    SOURCES, EXPECTED_PROJECTS, complete_pass, full_generation,
    historical_availability, legacy_sql_equivalence, raw_verification,
)

REFERENCES = {source: "load_21a8003352184861_" + source for source in SOURCES}
RAW_ROWS = {"p6_22": 2201435, "p6_23": 5520985}
SOURCE_COUNTS = {
    "p6_22": {"projects": 820, "activities": 296902, "relationships": 448377},
    "p6_23": {"projects": 2384, "activities": 912216, "relationships": 1523730},
}
RESOURCES = {
    "postgres_cpu_quota": 4, "postgres_memory_bytes": 6442450944,
    "calculation_session_work_mem_mb": 64, "calculation_session_jit": False,
    "raw_table_workers": 2, "source_calculation_processes": 2,
}
NEW_SUITES = {"test_batch_evidence", "test_pipelined_trial", "summarize_speedup"}


def positive_number(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def distribution(values):
    if not values or not all(positive_number(v) for v in values):
        return None
    return {"runs": len(values), "minimum_seconds": min(values),
            "maximum_seconds": max(values), "mean_seconds": statistics.mean(values)}


def stage_seconds(quality, name, depth):
    matches = [s for s in quality.get("stage_timings", [])
               if s.get("stage") == name and s.get("depth") == depth]
    if len(matches) != 1 or matches[0].get("status") != "passed":
        return None
    value = matches[0].get("seconds")
    return value if positive_number(value) else None


def timeline_for(timed, sources):
    """Retain recorded overlap; label the old serial harness's reconstruction."""
    if timed.get("timeline"):
        return timed["timeline"]
    cursor, timeline = 0.0, []
    for source in SOURCES:
        for stage, key in (("snapshot", "snapshot_seconds"), ("transfer", "transfer_seconds")):
            duration = sources[source].get(key)
            if not positive_number(duration):
                return []
            timeline.append({"stage": stage, "source": source, "start_seconds": cursor,
                             "end_seconds": cursor + duration,
                             "completion_timing": "inferred_from_sequential_durations"})
            cursor += duration
    helper = timed.get("helper_setup_seconds")
    parallel = timed.get("calculation", {}).get("parallel_calculation_seconds")
    if positive_number(helper) and positive_number(parallel):
        cursor += helper
        timeline.append({"stage": "parallel_calculations", "source": None,
                         "start_seconds": cursor, "end_seconds": cursor + parallel,
                         "completion_timing": "inferred_from_sequential_durations"})
    total, publication = timed.get("total_seconds"), timed.get("publication_seconds")
    if positive_number(total) and positive_number(publication):
        timeline.append({"stage": "publication", "source": None,
                         "start_seconds": total - publication, "end_seconds": total,
                         "completion_timing": "inferred_from_sequential_durations"})
    return timeline


def valid_pipeline_timeline(trial):
    rows = trial.get("timeline", [])
    total = trial.get("seconds")
    expected = {("helper_setup", None), ("publication", None)} | {
        (stage, source) for stage in ("snapshot", "transfer", "calculation_process") for source in SOURCES}
    if not positive_number(total) or len(rows) != len(expected):
        return False
    entries = {}
    for row in rows:
        start, end = row.get("start_seconds"), row.get("end_seconds")
        key = (row.get("stage"), row.get("source"))
        if (key not in expected or key in entries
                or type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= total + 0.001
                or row.get("completion_timing") not in ("recorded", "parent_observed")):
            return False
        entries[key] = row
    if set(entries) != expected:
        return False
    helper = entries["helper_setup", None]
    publication = entries["publication", None]
    for source in SOURCES:
        snapshot, transfer, calc = [entries[stage, source] for stage in ("snapshot", "transfer", "calculation_process")]
        if not (snapshot["end_seconds"] <= transfer["start_seconds"]
                and transfer["end_seconds"] <= calc["start_seconds"]
                and helper["end_seconds"] <= calc["start_seconds"]
                and calc["end_seconds"] <= publication["start_seconds"]):
            return False
    calc23 = entries["calculation_process", "p6_23"]
    snapshot22 = entries["snapshot", "p6_22"]
    transfer22 = entries["transfer", "p6_22"]
    return (entries["snapshot", "p6_23"]["start_seconds"] < snapshot22["start_seconds"]
            and calc23["start_seconds"] <= snapshot22["start_seconds"]
            and transfer22["start_seconds"] < calc23["end_seconds"])


def trial_summary(tag, timed, transfers):
    sources = {}
    for source in SOURCES:
        transfer = transfers[source]
        calculation = timed.get("calculation", {}).get("sources", {}).get(source, {})
        quality = calculation.get("quality", {})
        coverage = quality.get("project_coverage", {})
        sources[source] = {
            "raw_rows": transfer.get("rows"),
            "raw_values_verified": raw_verification(transfer, source)
                and transfer.get("rows") == RAW_ROWS[source]
                and transfer.get("schema") == calculation.get("candidate_schema"),
            "all_projects_processed": quality.get("status") == "success"
                and quality.get("processed_project_count") == EXPECTED_PROJECTS[source]
                and quality.get("config_version_id") == 37
                and quality.get("evidence_count_checks_passed") == 10
                and quality.get("project_scope_restricted") is False
                and coverage == {"source_projects": EXPECTED_PROJECTS[source],
                                 "result_projects": EXPECTED_PROJECTS[source],
                                 "missing_projects": 0, "extra_projects": 0}
                and calculation.get("reference_schema") == REFERENCES[source],
            "session_and_helpers_verified": quality.get("session_tuning") == {
                "work_mem_mb": 64, "jit": False}
                and quality.get("precreated_helpers", {}).get("verified") is True
                and all(s.get("status") == "passed" for s in quality.get("stage_timings", [])),
            "project_count": quality.get("processed_project_count"),
            "snapshot_seconds": timed.get("snapshot_stages", {}).get(source),
            "transfer_seconds": transfer.get("seconds"),
            "calculation_seconds": calculation.get("calculation_seconds"),
            "evidence_seconds": stage_seconds(quality, "calculate_evidence", 0),
            "renderer_seconds": stage_seconds(quality, "render_configured_evidence", 1),
            "candidate_schema": calculation.get("candidate_schema"),
        }
    publication = timed.get("historical_publication", {})
    scope = (timed.get("tag") == tag
             and timed.get("calculation_scope") == "all_projects"
             and timed.get("project_scope_restricted") is False
             and timed.get("calculation", {}).get("scope") == "all_projects"
             and timed.get("calculation", {}).get("project_scope_restricted") is False
             and timed.get("source_counts") == SOURCE_COUNTS
             and timed.get("fully_isolated") is True)
    timing_ok = (positive_number(timed.get("total_seconds"))
                 and positive_number(timed.get("publication_seconds"))
                 and all(positive_number(s.get(k)) for s in sources.values()
                         for k in ("snapshot_seconds", "transfer_seconds", "calculation_seconds",
                                   "evidence_seconds", "renderer_seconds")))
    historical_publication = (publication.get("scope") == "all_projects"
                             and publication.get("source_relations") == 94
                             and publication.get("combined_relations") == 47
                             and set(publication.get("reader_roles", [])) == {
                                 "p6_reporting_reader", "p6_reporting_external"})
    return {"tag": tag, "token": timed.get("token"), "seconds": timed.get("total_seconds"),
            "scope_all_projects": scope, "timing_complete": timing_ok,
            "historical_publication_complete": historical_publication,
            "raw_table_workers": timed.get("raw_table_workers"),
            "source_calculation_processes": timed.get("source_calculation_processes"),
            "helpers_precreated": timed.get("helpers_precreated"),
            "oos_disable_nestloop": timed.get("oos_disable_nestloop"),
            "optimize_oos": timed.get("optimize_oos"),
            "publication_seconds": timed.get("publication_seconds"),
            "timeline": timeline_for(timed, sources), "sources": sources}


def valid_provenance(manifest, before, after):
    if not (manifest.get("complete") is True
            and manifest.get("source_snapshot_time_utc") == "2026-11-16T16:32:50.820811+00:00"
            and manifest.get("settings_version") == 37
            and manifest.get("reference_schemas") == REFERENCES
            and all(manifest.get("resources", {}).get(k) == v for k, v in RESOURCES.items())):
        return False
    runs = manifest.get("runs", {})
    for group, tags in (("before", before), ("after", after)):
        for tag in tags:
            run = runs.get(tag, {})
            if not (run.get("group") == group
                    and run.get("resources_verified") is True
                    and run.get("same_fixed_source_inputs") is True
                    and run.get("same_settings") is True
                    and all(re.fullmatch(r"[0-9a-f]{64}", str(run.get(k, "")))
                            for k in ("renderer_sha256", "pipeline_sha256"))):
                return False
    for key in ("renderer_sha256", "pipeline_sha256"):
        original = {runs[t][key] for t in before}
        changed = {runs[t][key] for t in after}
        if len(original) != 1 or len(changed) != 1 or original == changed:
            return False
    return True


def summarize(directory, before, after, qa_tag):
    missing, unreadable = [], []
    def read(name):
        path = directory / name
        if not path.is_file():
            missing.append(name)
            return {}
        try:
            result = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(result, dict):
                raise ValueError("receipt is not an object")
            return result
        except (ValueError, OSError) as error:
            unreadable.append({"file": name, "error": str(error)})
            return {}

    groups, timed_receipts = {}, {}
    for group, tags in (("before", before), ("after", after)):
        groups[group] = []
        for tag in tags:
            timed = read(tag + "_end_to_end.json")
            timed_receipts[tag] = timed
            transfers = {s: read(tag + "_" + s + ".json") for s in SOURCES}
            groups[group].append(trial_summary(tag, timed, transfers))
    trials = groups["before"] + groups["after"]
    full = {s: read("all_scope_parity_" + qa_tag + "_" + s + ".json") for s in SOURCES}
    history = read("historical_availability_" + qa_tag + ".json")
    filtered = read("all_scope_filtered_parity_" + qa_tag + ".json")
    derived = read("all_scope_derived_parity_" + qa_tag + ".json")
    indices = read("all_scope_index_policy_" + qa_tag + ".json")
    qa = read("all_scope_qa_" + qa_tag + ".json")
    inventory = read("all_projects_legacy_view_inventory.json")
    sql = read("legacy_sql_equivalence.json")
    sql_calculation = read("legacy_sql_oracle_calculation.json")
    units = read("unit-tests.json")
    negative = read("native_verification_negative_control.json")
    provenance = read("speedup_experiment.json")
    baseline = read("baseline.json")
    stats = {g: distribution([t["seconds"] for t in ts]) for g, ts in groups.items()}
    improvement = None
    if all(stats.values()):
        old, new = stats["before"]["mean_seconds"], stats["after"]["mean_seconds"]
        improvement = {"seconds_saved_on_mean": old - new,
                       "runtime_reduction_percent": (old - new) / old * 100,
                       "speed_ratio": old / new}
    final = next((t for t in groups["after"] if t["tag"] == qa_tag), {})
    conditions = {
        "two_distinct_before_and_two_after_runs": len(before) >= 2 and len(after) >= 2
            and len(before + after) == len(set(before + after)) and qa_tag in after
            and all(re.fullmatch(r"[0-9a-f]{16}", str(t.get("token", ""))) for t in trials)
            and len({t["token"] for t in trials}) == len(trials),
        "same_fixed_inputs_settings_resources_and_code_bound_to_runs": valid_provenance(provenance, before, after),
        "all_runs_complete_and_all_projects": all(t["scope_all_projects"] and t["timing_complete"]
                                                and t["historical_publication_complete"] for t in trials),
        "full_raw_verification_inside_every_run": all(s["raw_values_verified"] for t in trials for s in t["sources"].values()),
        "all_3204_projects_and_20_reconciliations_each_run": all(s["all_projects_processed"] for t in trials for s in t["sources"].values()),
        "same_local_tuning_and_precreated_helpers": all(t["raw_table_workers"] == 2
            and t["source_calculation_processes"] == 2 and t["helpers_precreated"] is True
            and t["oos_disable_nestloop"] is True and t["optimize_oos"] is False
            and all(s["session_and_helpers_verified"] for s in t["sources"].values()) for t in trials),
        "all_after_runs_within_180_seconds": all(positive_number(t["seconds"]) and t["seconds"] <= 180 for t in groups["after"]),
        "larger_source_pipeline_overlap_and_atomic_publication_recorded": all(valid_pipeline_timeline(t) for t in groups["after"]),
        "measured_mean_improved": improvement is not None and improvement["seconds_saved_on_mean"] > 0,
        "final_all_118_unfiltered_relations_match_exactly": all(full_generation(full[s], s)
            and full[s].get("reference_schema") == REFERENCES[s]
            and full[s].get("candidate_schema") == final.get("sources", {}).get(s, {}).get("candidate_schema") for s in SOURCES),
        "all_history_and_reader_access_retained": historical_availability(history),
        "existing_40_filtered_reports_and_two_selections_match": complete_pass(filtered, 42) and filtered.get("metadata_exclusions") == [],
        "existing_26_derived_reports_match": complete_pass(derived, 26) and derived.get("metadata_exclusions") == [],
        "original_42_raw_index_policies_match": complete_pass(indices, 42),
        "offline_qa_complete": qa.get("complete") is True and qa.get("passed") is True
            and qa.get("scope") == "all_projects" and len(qa.get("gates", {})) == 6
            and all(v is True for v in qa.get("gates", {}).values()),
        "all_20_legacy_SQL_view_names_retained": inventory.get("complete") is True
            and inventory.get("passed") is True and inventory.get("legacy_view_count") == 20,
        "same_snapshot_original_SQL_equivalence_preserved": legacy_sql_equivalence(sql, sql_calculation)
            and all(sql.get("sources", {}).get(s, {}).get("reference_schema") == REFERENCES[s] for s in SOURCES),
        "updated_regression_suites_pass_without_skips": units.get("passed") is True
            and units.get("failures") == 0 and units.get("errors") == 0 and units.get("skipped") == 0
            and units.get("run", 0) > 57 and NEW_SUITES <= set(units.get("suites", [])),
        "same_count_corruption_rejected_and_restored": negative.get("same_row_count_corruption_detected") is True
            and negative.get("restored_full_value_verification") == "passed",
        "all_required_evidence_present_and_readable": not missing and not unreadable,
    }
    return {
        "accepted_for_fixed_snapshot": all(conditions.values()),
        "production_deployed": False, "target_seconds": 180,
        "calculation_scope": "all_projects_and_history", "qa_tag": qa_tag,
        "expected_project_count": sum(EXPECTED_PROJECTS.values()), "raw_rows_per_run": sum(RAW_ROWS.values()),
        "groups": groups, "statistics": stats, "improvement": improvement,
        "conditions": conditions, "missing_evidence": missing, "unreadable_evidence": unreadable,
        "provenance": provenance, "prior_live_baseline_seconds": baseline.get("seconds"),
        "timing_boundary": "SQL capture/checksum verification/restore; raw copy, original indexes and complete value verification; all-project calculation; atomic latest and historical publication.",
        "offline_qa": "Full reference/report/SQL-equivalence acceptance is additional to the refresh clock. The original SQL oracle is reused through the unchanged, fixed PostgreSQL reference.",
        "limitations": ["Fixed-snapshot isolated prototype, not a production deployment or a future runtime guarantee.",
                        "The original live 479-second refresh is context; only fresh same-resource before/after trials measure these two new fixes.",
                        "Queue wait and full production audit/checkpoint/retention orchestration are outside this core-refresh trial.",
                        "Evidence renderer time is contained in evidence calculation, which is contained in source calculation. Concurrent source durations are not additive."],
    }


def render_report(result):
    accepted = result["accepted_for_fixed_snapshot"]
    text = ["# P6 refresh speed comparison", "", "Acceptance: **" + ("passed" if accepted else "pending or failed") + "**.", "",
            "These are isolated, fixed-snapshot measurements with all 3,204 projects, historical reporting and complete verification of all 7,722,420 raw rows.", "",
            "| Version | Run | Complete refresh |", "| --- | --- | ---: |"]
    for group in ("before", "after"):
        for trial in result["groups"][group]:
            value = trial["seconds"]
            duration = f"{value:.3f}s ({int(value // 60)}m{value % 60:05.2f}s)" if positive_number(value) else "missing"
            text.append(f"| {group.title()} | `{trial['tag']}` | {duration} |")
    improvement = result["improvement"]
    if improvement:
        old = result["statistics"]["before"]["mean_seconds"]
        new = result["statistics"]["after"]["mean_seconds"]
        text += ["", f"Mean: **{old:.3f}s before → {new:.3f}s after**; **{improvement['seconds_saved_on_mean']:.3f}s saved ({improvement['runtime_reduction_percent']:.1f}% shorter runtime)**."]
    text += ["", "The changes batch the configured evidence fields and start the larger P6 23 calculation as soon as its verified transfer finishes, overlapping the P6 22 transfer. Both sources publish together after both calculations succeed.", "",
             "| Source and stage | Before mean | After mean |", "| --- | ---: | ---: |"]
    for source in SOURCES:
        for key, label in (("transfer_seconds", "raw transfer + full verification"),
                           ("calculation_seconds", "complete calculation"),
                           ("evidence_seconds", "evidence calculation, included above"),
                           ("renderer_seconds", "evidence formatting, included above")):
            cells = []
            for group in ("before", "after"):
                dist = distribution([t["sources"][source][key] for t in result["groups"][group]])
                cells.append(f"{dist['mean_seconds']:.3f}s" if dist else "missing")
            text.append(f"| {source} {label} | {cells[0]} | {cells[1]} |")
    text += ["", "Source calculations overlap. Evidence formatting is part of evidence calculation, and both sit inside the source calculation; these rows must not be added together.", "",
             "## Whole-job timeline", "", "Times below are seconds from the start of each refresh.", "",
             "| Run | P6 23 verified transfer finished | P6 23 calculation started | P6 22 transfer interval | Atomic publication interval |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for group in ("before", "after"):
        for trial in result["groups"][group]:
            rows = {(r.get("stage"), r.get("source")): r for r in trial["timeline"]}
            verified = rows.get(("transfer", "p6_23"), {}).get("end_seconds")
            calc = rows.get(("calculation_process", "p6_23"), rows.get(("parallel_calculations", None), {})).get("start_seconds")
            def instant(value):
                return f"{value:.3f}s" if type(value) in (int, float) and math.isfinite(value) else "missing"
            def interval(key):
                row = rows.get(key, {})
                return instant(row.get("start_seconds")) + "–" + instant(row.get("end_seconds"))
            text.append(f"| `{trial['tag']}` | {instant(verified)} | {instant(calc)} | {interval(('transfer', 'p6_22'))} | {interval(('publication', None))} |")
    text += ["", "Before-run positions are reconstructed from the serial harness's stage durations and omit small orchestration gaps. After-run boundaries are recorded by the parent process. Calculation completion is observed when the parent next polls the process, so that boundary may lag the actual exit; the separate SQL calculation duration above is measured inside the child.", "",
             "## Correctness evidence", ""]
    text += [f"- {'PASS' if passed else 'FAIL/PENDING'}: `{name}`." for name, passed in result["conditions"].items()]
    text += ["", "The final candidate is compared to the unchanged fixed reference in all 118 unfiltered relations, including every business and generated field. Historical reader access, existing filtered reports, derived reports and raw index policies are checked separately. The independent original SQL replay already matched this same fixed reference in all 40 report outputs, covering 13,452,784 report rows; exact candidate-to-reference parity preserves that proof.", "",
             "## Reproduction boundary", "", result["timing_boundary"], "", result["offline_qa"], "",
             "Resources: PostgreSQL 4 CPUs and 6 GiB, 64 MiB calculation-session work memory with JIT off, two raw-transfer workers and two independent source calculation processes. Both helper catalogues are prepared and committed before the long calculations. Source snapshots are from 16 November 2026 16:32 UTC; settings version 37.", ""]
    text += ["- " + note for note in result["limitations"]]
    text += ["", "Retain `speedup-comparison-summary.json`, `speedup_experiment.json`, every named run receipt and the final QA receipts together to revisit the measurements.", ""]
    return "\n".join(text)


def self_test():
    import unittest
    from copy import deepcopy

    class SpeedupPolicyTests(unittest.TestCase):
        def test_invalid_stopwatch_values_rejected(self):
            for value in (None, True, 0, -1, float("nan"), float("inf"), "123"):
                self.assertIsNone(distribution([value]))

        def test_statistics_use_fresh_run_mean(self):
            self.assertEqual(distribution([160, 164]), {"runs": 2, "minimum_seconds": 160, "maximum_seconds": 164, "mean_seconds": 162})

        def test_nested_stage_is_not_summed(self):
            quality = {"stage_timings": [{"stage": "calculate_evidence", "depth": 0, "seconds": 15, "status": "passed"},
                                        {"stage": "render_configured_evidence", "depth": 1, "seconds": 10, "status": "passed"}]}
            self.assertEqual(stage_seconds(quality, "calculate_evidence", 0), 15)
            self.assertEqual(stage_seconds(quality, "render_configured_evidence", 1), 10)
            self.assertIsNone(stage_seconds(quality, "render_configured_evidence", 0))

        def test_duplicate_or_failed_stage_rejected(self):
            row = {"stage": "render_configured_evidence", "depth": 1, "seconds": 1, "status": "passed"}
            self.assertIsNone(stage_seconds({"stage_timings": [row, row]}, row["stage"], 1))
            self.assertIsNone(stage_seconds({"stage_timings": [{**row, "status": "failed"}]}, row["stage"], 1))

        def test_provenance_requires_changed_code_and_verified_resources(self):
            manifest = {"complete": True, "source_snapshot_time_utc": "2026-11-16T16:32:50.820811+00:00", "settings_version": 37,
                        "reference_schemas": REFERENCES, "resources": RESOURCES, "runs": {}}
            for group, tags, digest in (("before", ["b1", "b2"], "a"), ("after", ["a1", "a2"], "b")):
                for tag in tags:
                    manifest["runs"][tag] = {"group": group, "renderer_sha256": digest * 64, "pipeline_sha256": digest * 64,
                                             "resources_verified": True, "same_fixed_source_inputs": True, "same_settings": True}
            self.assertTrue(valid_provenance(manifest, ["b1", "b2"], ["a1", "a2"]))
            bad = deepcopy(manifest);bad["runs"]["a2"]["resources_verified"] = False
            self.assertFalse(valid_provenance(bad, ["b1", "b2"], ["a1", "a2"]))
            bad = deepcopy(manifest);bad["runs"]["a2"]["renderer_sha256"] = "c" * 64
            self.assertFalse(valid_provenance(bad, ["b1", "b2"], ["a1", "a2"]))

        def test_missing_receipts_fail_closed(self):
            import tempfile
            with tempfile.TemporaryDirectory() as directory:
                result = summarize(Path(directory), ["b1", "b2"], ["a1", "a2"], "a2")
                self.assertFalse(result["accepted_for_fixed_snapshot"])
                self.assertGreater(len(result["missing_evidence"]), 10)
                self.assertIsNone(result["improvement"])

        def test_latest_only_receipt_rejected(self):
            result = trial_summary("scoped", {"calculation_scope": "latest_projects", "project_scope_restricted": True},
                                   {source: {} for source in SOURCES})
            self.assertFalse(result["scope_all_projects"])
            self.assertTrue(all(not s["all_projects_processed"] and not s["raw_values_verified"] for s in result["sources"].values()))

        def test_pipeline_requires_complete_transfer_before_calculation_and_publication_after_both(self):
            events = [("helper_setup", None, 0, 1), ("snapshot", "p6_23", 1, 10),
                      ("transfer", "p6_23", 10, 40), ("calculation_process", "p6_23", 40, 110),
                      ("snapshot", "p6_22", 40, 44), ("transfer", "p6_22", 44, 60),
                      ("calculation_process", "p6_22", 60, 100), ("publication", None, 110, 115)]
            trial = {"seconds": 115, "timeline": [{"stage": stage, "source": source, "start_seconds": start,
                      "end_seconds": end, "completion_timing": "recorded"} for stage, source, start, end in events]}
            self.assertTrue(valid_pipeline_timeline(trial))
            bad = deepcopy(trial);bad["timeline"][3]["start_seconds"] = 39
            self.assertFalse(valid_pipeline_timeline(bad))
            bad = deepcopy(trial);bad["timeline"][-1]["start_seconds"] = 99
            self.assertFalse(valid_pipeline_timeline(bad))

        def test_sequential_after_run_cannot_claim_pipeline_overlap(self):
            self.assertFalse(valid_pipeline_timeline({"seconds": 160, "timeline": []}))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SpeedupPolicyTests)
    return unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("--before", nargs="+")
    parser.add_argument("--after", nargs="+")
    parser.add_argument("--qa-tag")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        raise SystemExit(0 if self_test() else 1)
    if not args.directory or not args.before or not args.after or not args.qa_tag:
        parser.error("Supply the artifact directory, --before, --after and --qa-tag")
    if any(not re.fullmatch(r"[A-Za-z0-9_]+", tag) for tag in args.before + args.after + [args.qa_tag]):
        parser.error("Use explicit simple trial tags")
    result = summarize(args.directory, args.before, args.after, args.qa_tag)
    (args.directory / "speedup-comparison-summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.report:
        args.report.write_text(render_report(result), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("accepted_for_fixed_snapshot", "statistics", "improvement", "conditions", "missing_evidence", "unreadable_evidence")}, indent=2))
    raise SystemExit(0 if result["accepted_for_fixed_snapshot"] else 1)
