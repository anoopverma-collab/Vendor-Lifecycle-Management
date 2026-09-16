# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def execute():
	# All of this master/template seeding used to live in after_migrate(),
	# re-checked (via "does this record exist" checks) on every single
	# migrate. That meant deleting a seeded master or template — a
	# deliberate customization choice — silently undid itself on the very
	# next migrate, since the code couldn't tell "deliberately removed"
	# apart from "never created yet". A Patch is the right tool here
	# instead: Frappe tracks it in Patch Log and guarantees it only ever
	# runs once per site, no matter how many times bench migrate runs
	# afterward — so a deleted record now actually stays deleted.
	#
	# Order matches exactly how these used to run inside after_migrate(),
	# preserving the one real dependency between them: backfill_default_
	# rating_criteria_template() reuses the same Rating Criteria rows
	# backfill_default_satisfaction_rating_template() creates (it also
	# re-ensures them defensively itself, but the order is kept anyway).
	from vendor_lifecycle.vendor_lifecycle.install import (
		_ensure_compliance_check_types,
		_ensure_default_compliance_check_template,
		_ensure_default_checklist_template,
		backfill_default_satisfaction_rating_template,
		backfill_default_compliance_check_sources,
		backfill_default_coverage_types,
		backfill_default_licenses_and_permits_masters,
		remove_stale_generic_insurers,
		backfill_default_insurance_masters,
		backfill_default_rating_criteria_template,
		backfill_default_sampling_evaluation_template,
		seed_indian_states,
		backfill_default_deboarding_rating_template,
		backfill_default_deboarding_checklist_template,
	)

	_ensure_compliance_check_types()
	_ensure_default_compliance_check_template()
	_ensure_default_checklist_template()
	backfill_default_satisfaction_rating_template()
	backfill_default_compliance_check_sources()
	backfill_default_coverage_types()
	backfill_default_licenses_and_permits_masters()
	remove_stale_generic_insurers()
	backfill_default_insurance_masters()
	backfill_default_rating_criteria_template()
	backfill_default_sampling_evaluation_template()
	seed_indian_states()
	backfill_default_deboarding_rating_template()
	backfill_default_deboarding_checklist_template()
