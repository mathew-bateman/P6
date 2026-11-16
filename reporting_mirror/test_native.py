from datetime import datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from reporting_mirror.native_engine import cycle_core, generation_schema, query_definition
from reporting_mirror.native_settings import encode, decode, content_hash, restore_checkpoint
from reporting_mirror.native_validation import canonical, compare_outputs, report_contracts
from reporting_mirror.models import ReportingConfig, ReportingRun, NativeEngineValidation


class NativeSemanticsTests(SimpleTestCase):
    def test_self_cycles_do_not_turn_dag_neighbours_into_loops(self):
        self.assertEqual(cycle_core([(1,1),(2,3),(3,2),(4,2),(3,5),(7,8)]),({1},{2,3}))

    def test_bridge_between_cycles_preserves_existing_directed_core_semantics(self):
        self.assertEqual(cycle_core([(1,2),(2,1),(2,3),(3,4),(4,5),(5,4)])[1],{1,2,3,4,5})

    def test_long_chain_is_not_recursive(self):
        self.assertEqual(cycle_core((i,i+1) for i in range(20000)),(set(),set()))

    def test_native_build_refuses_published_or_arbitrary_schema(self):
        for name in ['p6_22','public','quality_control','native_test_BAD_p6_22','load_0000000000000000_p6_22;DROP SCHEMA public']:
            with self.assertRaises(ValueError):generation_schema(name)

    def test_definition_substitution_never_accepts_sql_from_configuration(self):
        with self.assertRaises(ValueError):query_definition('xertoolkit_vw_pbi_projects','load_0000000000000000_p6_22','1;DROP TABLE task')

    def test_portable_settings_losslessly_encode_binary_dates_and_decimals(self):
        payload={'number':Decimal('2.50'),'date':datetime(2026,10,25,12,0),'row':b'\x00\xff'}
        self.assertEqual(decode(encode(payload)),payload)
        self.assertEqual(content_hash(payload),content_hash(encode(payload)))

    def test_recovery_checksum_failure_cannot_issue_sql(self):
        pg=MagicMock()
        with self.assertRaises(ValueError):restore_checkpoint(pg,{},'bad')
        pg.execute.assert_not_called()

    def test_json_comparison_ignores_syntax_but_preserves_business_values(self):
        self.assertEqual(canonical('{"b":2,"a":"x/y"}','detail_json'),canonical('{ "a":"x\\/y", "b":2 }','detail_json'))
        self.assertNotEqual(canonical('{"a":"One, Two"}','detail_json'),canonical('{"a":"Two, One"}','detail_json'))

    def test_validation_rejects_missing_outputs_before_comparing_shared_subset(self):
        with self.assertRaisesMessage(RuntimeError,'incomplete'):
            compare_outputs(MagicMock(),{'p6_22':{'manifest':[]}})

    def test_reference_catalog_compares_only_the_twenty_report_views(self):
        for source in ('p6_22','p6_23'):
            contracts=report_contracts(source)
            self.assertEqual(len(contracts),20)
            self.assertNotIn('actvcode',contracts)
            self.assertIn('xertoolkit_vw_pbi_openends',contracts)


class NativeControlsTests(TestCase):
    def setUp(self):
        self.staff=get_user_model().objects.create_user(username='native-admin',is_staff=True)
        self.normal=get_user_model().objects.create_user(username='native-reader')
        self.config=ReportingConfig.current()
        self.client.force_login(self.staff)

    @patch('reporting_mirror.native_settings.enabled',return_value=True)
    @patch('reporting_mirror.tasks.refresh_reporting')
    @patch('backups.tasks.refresh_schedule_quality')
    def test_manual_quality_refresh_fetches_fresh_P6_data(self,legacy_sql,task,enabled):
        from backups.tasks import execute_schedule_quality_refresh
        execute_schedule_quality_refresh.run(proj_id=123,trigger='manual')
        legacy_sql.assert_not_called()
        queued=task.apply_async.call_args.kwargs
        self.assertEqual(queued['queue'],'p6_reporting')
        self.assertFalse(queued['kwargs']['recalculate_only'])

    @patch('reporting_mirror.views.reporting_available',return_value=True)
    @patch('reporting_mirror.views.validate_native_engine')
    def test_validation_is_staff_post_only_and_uses_dedicated_queue(self,task,available):
        self.assertEqual(self.client.get(reverse('reporting_mirror_validate_engine')).status_code,405)
        self.client.force_login(self.normal)
        self.assertEqual(self.client.post(reverse('reporting_mirror_validate_engine')).status_code,403)
        task.apply_async.assert_not_called()
        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(reverse('reporting_mirror_validate_engine')).status_code,302)
        self.assertEqual(task.apply_async.call_args.kwargs['queue'],'p6_reporting')
        self.assertEqual(NativeEngineValidation.objects.get().status,'queued')

    @patch('reporting_mirror.views.reporting_available',return_value=True)
    @patch('reporting_mirror.views.validate_native_engine')
    def test_duplicate_validation_is_not_queued(self,task,available):
        NativeEngineValidation.objects.create(status='running')
        self.client.post(reverse('reporting_mirror_validate_engine'))
        task.apply_async.assert_not_called()

    @patch('reporting_mirror.views.reporting_available',return_value=True)
    @patch('reporting_mirror.views.validate_native_engine')
    def test_broker_failure_marks_validation_failed(self,task,available):
        task.apply_async.side_effect=RuntimeError('broker unavailable')
        self.client.post(reverse('reporting_mirror_validate_engine'))
        self.assertEqual(NativeEngineValidation.objects.get().status,'failed')
        self.config.refresh_from_db();self.assertEqual(self.config.calculation_engine,'sql')

    @patch('reporting_mirror.forms.reporting_available',return_value=True)
    @patch('reporting_mirror.native_pipeline.verified_native_engine',return_value=None)
    def test_engine_switch_requires_current_validation(self,validation,available):
        from reporting_mirror.forms import ReportingSettingsForm
        form=ReportingSettingsForm({'calculation_engine':'postgres','interval_minutes':60,'stale_after_minutes':120,'query_timeout_seconds':60,'retained_generations':1},instance=self.config)
        self.assertFalse(form.is_valid());self.assertIn('calculation_engine',form.errors)

    @patch('reporting_mirror.forms.reporting_available',return_value=True)
    def test_native_rules_cannot_be_served_from_stale_legacy_sql(self,available):
        from reporting_mirror.forms import ReportingSettingsForm
        self.config.manage_settings_in_postgres=True;self.config.serve_reports=True;self.config.save()
        ReportingRun.objects.create(status='success')
        form=ReportingSettingsForm({'calculation_engine':'sql','interval_minutes':60,'stale_after_minutes':120,'query_timeout_seconds':60,'retained_generations':1},instance=self.config)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertTrue(form.cleaned_data['serve_reports'])

    @patch('reporting_mirror.forms.reporting_available',return_value=True)
    @patch('reporting_mirror.native_pipeline.verified_native_engine',return_value=object())
    @patch('reporting_mirror.views.switch_reporting_engine')
    def test_switch_waits_for_successful_publication(self,task,validation,available):
        response=self.client.post(reverse('reporting_mirror_settings'),{'enabled':'on','calculation_engine':'postgres','interval_minutes':60,'stale_after_minutes':120,'query_timeout_seconds':60,'retained_generations':1})
        self.assertEqual(response.status_code,302)
        self.config.refresh_from_db();self.assertEqual(self.config.calculation_engine,'sql');self.assertEqual(self.config.pending_engine,'postgres')
        self.assertEqual(task.apply_async.call_args.kwargs['queue'],'p6_reporting')
        from django_celery_beat.models import PeriodicTask
        schedule=PeriodicTask.objects.get(name='P6 Combined PostgreSQL Reporting Refresh')
        self.assertIsNotNone(schedule.last_run_at)

    @patch('reporting_mirror.tasks.run_sync',create=True)
    def test_failed_engine_task_clears_pending_without_changing_engine(self,unused):
        from reporting_mirror.tasks import switch_reporting_engine
        self.config.pending_engine='postgres';self.config.save()
        with patch('reporting_mirror.sync.run_sync',side_effect=RuntimeError('rebuild failed')):
            with self.assertRaises(RuntimeError):switch_reporting_engine.run(engine='postgres')
        self.config.refresh_from_db();self.assertEqual(self.config.calculation_engine,'sql');self.assertEqual(self.config.pending_engine,'')

    @patch('reporting_mirror.native_pipeline.postgres_connection',side_effect=RuntimeError('database unavailable'))
    def test_native_failure_keeps_last_good_publication(self,connection):
        from reporting_mirror.native_pipeline import run_native_sync
        old=ReportingRun.objects.create(status='success')
        with self.assertRaises(RuntimeError):run_native_sync(force=True)
        self.assertEqual(ReportingRun.objects.get(status='success').pk,old.pk)
        self.assertEqual(ReportingRun.objects.get(status='failed').engine,'postgres')
