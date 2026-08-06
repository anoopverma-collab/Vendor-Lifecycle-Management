# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, nowdate

CADENCE_DAYS = {
	"Monthly": 30,
	"Quarterly": 90,
	"Half-Yearly": 180,
	"Yearly": 365,
}


def create_pending_satisfaction_surveys():
	"""Daily scheduler job: for every Active vendor without a survey inside
	the configured cadence window, create a new (draft) one."""
	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not settings.enable_satisfaction_surveys:
		return []

	cutoff = add_days(nowdate(), -CADENCE_DAYS.get(settings.survey_cadence, 90))
	active_vendors = frappe.get_all(
		"Supplier",
		filters={"vendor_lifecycle_status": "Active", "disabled": 0},
		pluck="name",
	)

	created = []
	for vendor in active_vendors:
		has_recent_survey = frappe.db.exists(
			"Vendor Satisfaction Survey", {"vendor": vendor, "survey_date": [">=", cutoff]}
		)
		if has_recent_survey:
			continue

		doc = frappe.get_doc({
			"doctype": "Vendor Satisfaction Survey",
			"vendor": vendor,
			"period": settings.survey_cadence,
			"survey_date": nowdate(),
		}).insert(ignore_permissions=True)
		created.append(doc.name)

	return created
