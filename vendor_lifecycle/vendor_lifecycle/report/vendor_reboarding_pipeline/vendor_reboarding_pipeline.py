# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _

NOT_STARTED_FIELDS = (
	"background_check_status",
	"compliance_audit_status",
	"sampling_evaluation_status",
	"sign_off_status",
)


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns() -> list[dict]:
	return [
		{"label": _("Reboarding Request"), "fieldname": "request", "fieldtype": "Link", "options": "Vendor Reboarding Request", "width": 150},
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Vendor Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180},
		{"label": _("Request Status"), "fieldname": "request_status", "fieldtype": "Data", "width": 120},
		{"label": _("Request Date"), "fieldname": "request_date", "fieldtype": "Date", "width": 100},
		{"label": _("Background Check"), "fieldname": "background_check_status", "fieldtype": "Data", "width": 130},
		{"label": _("Compliance Audit"), "fieldname": "compliance_audit_status", "fieldtype": "Data", "width": 130},
		{"label": _("Sampling Evaluation"), "fieldname": "sampling_evaluation_status", "fieldtype": "Data", "width": 140},
		{"label": _("Sign Off"), "fieldname": "sign_off_status", "fieldtype": "Data", "width": 100},
	]


def get_data() -> list[dict]:
	# Each stage doctype is shared with onboarding (kyc-keyed) — every
	# subquery here is scoped by reboarding_request instead, so onboarding's
	# own history for the same vendor/kyc never leaks into this pipeline
	# view. Latest (by creation) record per stage, same as Vendor Onboarding
	# Pipeline's own KYC-anchored view — a stage redone during the same
	# re-boarding run shouldn't show a stale earlier attempt.
	rows = frappe.db.sql(
		"""
		select
			vrr.name as request,
			vrr.vendor,
			s.supplier_name,
			vrr.status as request_status,
			vrr.request_date,
			(
				select bc.overall_status
				from `tabVendor Background Check` bc
				where bc.reboarding_request = vrr.name
				order by bc.creation desc limit 1
			) as background_check_status,
			(
				select ca.outcome
				from `tabVendor Compliance Audit` ca
				where ca.reboarding_request = vrr.name
				order by ca.creation desc limit 1
			) as compliance_audit_status,
			(
				select se.evaluation_outcome
				from `tabVendor Sampling Evaluation` se
				where se.reboarding_request = vrr.name
				order by se.creation desc limit 1
			) as sampling_evaluation_status,
			(
				select case when so.docstatus = 1 then
					(case when so.sign_off_failed then 'Failed' else 'Signed' end)
				else 'Draft' end
				from `tabVendor Sign Off` so
				where so.reboarding_request = vrr.name
				order by so.creation desc limit 1
			) as sign_off_status
		from `tabVendor Reboarding Request` vrr
		left join `tabSupplier` s on s.name = vrr.vendor
		order by vrr.creation desc
		""",
		as_dict=True,
	)

	_fill_not_started(rows)
	return rows


def _fill_not_started(rows):
	for row in rows:
		for field in NOT_STARTED_FIELDS:
			row[field] = row.get(field) or _("Not Started")
