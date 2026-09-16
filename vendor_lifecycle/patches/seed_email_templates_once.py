# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# Same reasoning as seed_master_data_once.py - these used to re-check
	# and recreate themselves on every migrate, so deleting one (a
	# deliberate choice) silently undid itself on the next migrate. A
	# separate patch, not added to seed_master_data_once.py: that one has
	# already run (and been recorded as done) on sites that had already
	# migrated past it, so anything added to its body afterward would
	# never execute there - a new patch is what actually gets picked up.
	#
	# Order matches exactly how these used to run inside after_migrate().
	from vendor_lifecycle.vendor_lifecycle.install import (
		backfill_default_signoff_email_template,
		backfill_default_signoff_received_email_template,
		backfill_default_signoff_passed_email_template,
		backfill_default_signoff_failed_email_template,
		backfill_default_vendor_lifecycle_stage_email_templates,
		backfill_default_satisfaction_survey_email_templates,
		backfill_default_support_ticket_email_templates,
		backfill_default_deboarding_request_email_templates,
		backfill_default_checklist_task_email_templates,
		backfill_default_clearance_certificate_email_templates,
		backfill_default_signoff_followup_email_template,
		backfill_default_manual_attach_needed_email_template,
	)

	backfill_default_signoff_email_template()
	backfill_default_signoff_received_email_template()
	backfill_default_signoff_passed_email_template()
	backfill_default_signoff_failed_email_template()
	backfill_default_vendor_lifecycle_stage_email_templates()
	backfill_default_satisfaction_survey_email_templates()
	backfill_default_support_ticket_email_templates()
	backfill_default_deboarding_request_email_templates()
	backfill_default_checklist_task_email_templates()
	backfill_default_clearance_certificate_email_templates()
	backfill_default_signoff_followup_email_template()
	backfill_default_manual_attach_needed_email_template()
