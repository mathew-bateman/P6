"""Guard and comparison-policy tests for the independent legacy SQL oracle."""
import unittest
from sql_oracle import Oracle,private_database,normalize_module,oracle_projection,runtime_columns

class OraclePolicyTests(unittest.TestCase):
    def test_operational_and_unrecognised_databases_are_rejected(self):
        for name in ('P62212_1','Axial','reporting_stage_p6_22','perf_other','perf_sql_oracle_p6_22;DROP DATABASE Axial'):
            with self.subTest(name=name),self.assertRaises(ValueError):private_database(name)
        self.assertEqual(private_database('perf_sql_oracle_p6_22'),'perf_sql_oracle_p6_22')

    def test_machine_identity_is_checked_before_allowlisted_database(self):
        oracle=Oracle.__new__(Oracle)
        oracle.query=lambda *args:[{'database_name':'perf_sql_oracle_p6_22','machine':'xps4-p62212'}]
        with self.assertRaises(ValueError):oracle.assert_sql(object())
        oracle.query=lambda *args:[{'database_name':'P62212_1','machine':'p6-perf-sql'}]
        with self.assertRaises(ValueError):oracle.assert_sql(object())
        oracle.query=lambda *args:[{'database_name':'perf_sql_oracle_p6_22','machine':'p6-perf-sql'}]
        self.assertEqual(oracle.assert_sql(object())['machine'],'p6-perf-sql')

    def test_module_normalisation_preserves_body(self):
        definition='CREATE VIEW powerbitables.test AS SELECT 1 AS amount;'
        self.assertEqual(normalize_module(definition),'CREATE OR ALTER VIEW powerbitables.test AS SELECT 1 AS amount;')
        definition='CREATE OR ALTER PROCEDURE powerbitables.p AS SELECT 1;'
        self.assertEqual(normalize_module(definition),definition)

    def test_only_three_generated_columns_are_normalised(self):
        columns=[{'name':n,'type':'timestamp','category':'D'} for n in ('check_run_id','refreshed_at','calculated_date','data_date','updated_date','config_version_id','published_at')]
        self.assertEqual(runtime_columns(columns),['calculated_date','check_run_id','refreshed_at'])
        query=oracle_projection(columns).as_string()
        self.assertIn('("t"."check_run_id" IS NULL)',query)
        for name in ('data_date','updated_date','config_version_id','published_at'):
            self.assertNotIn('("t"."'+name+'" IS NULL)',query)
            self.assertIn('"t"."'+name+'" AS "'+name+'"',query)

    def test_json_property_order_is_syntax_but_null_empty_and_text_are_preserved(self):
        query=oracle_projection([{'name':'detail_json','type':'text','category':'S'},{'name':'task_name','type':'text','category':'S'}]).as_string()
        self.assertIn('"t"."detail_json" IS NULL OR "t"."detail_json"=\'\'',query)
        self.assertIn('::jsonb',query)
        self.assertIn('convert_to("t"."task_name"::text,\'UTF8\')',query)
        self.assertNotIn('lower(',query.lower())

class AllProjectAcceptancePolicyTests(unittest.TestCase):
    def fixture(self):
        import summarize_all_projects as summary
        rows=[]
        for kind,count in summary.KIND_COUNTS.items():
            rows.extend({'passed':True,'kind':kind,'publication_scoped':False,'normalized_metadata_columns':[]} for _ in range(count))
        return summary,{'complete':True,'passed':True,'scope':'all_projects','source':'p6_22','coverage':dict(summary.COVERAGE),'all_project_count':820,'reference_project_count':820,'metadata_exclusions':[],'publication_filter_applied':False,'results':rows}

    def test_complete_all_project_inventory_passes(self):
        summary,receipt=self.fixture()
        self.assertTrue(summary.full_generation(receipt,'p6_22'))

    def test_latest_only_filter_cannot_pass_with_full_raw_counts(self):
        summary,receipt=self.fixture();receipt['publication_filter_applied']=True
        self.assertFalse(summary.full_generation(receipt,'p6_22'))

    def test_missing_internal_history_table_cannot_pass(self):
        summary,receipt=self.fixture();receipt['results']=[r for r in receipt['results'] if r['kind']!='result']
        self.assertFalse(summary.full_generation(receipt,'p6_22'))

    def test_scoped_receipt_label_is_rejected(self):
        summary,receipt=self.fixture();receipt['scope']='latest_submitted_quality_trial'
        self.assertFalse(summary.full_generation(receipt,'p6_22'))

if __name__=='__main__':unittest.main()

