# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _

NOT_STARTED_FIELDS = (
	"kyc_status",
	"background_check_status",
	"compliance_audit_status",
	"sampling_evaluation_status",
	"sign_off_status",
)


def execute(filters: dict | None = None):
	filters = filters or {}
	view_by = filters.get("view_by")

	if not view_by:
		frappe.throw(_("Select a View By option (Onboarding Request or Vendor KYC) to see this report."))

	if view_by == "Onboarding Request":
		return get_request_columns(), get_request_data()

	return get_kyc_columns(), get_kyc_data()


def get_kyc_columns() -> list[dict]:
	return [
		{"label": _("KYC"), "fieldname": "kyc", "fieldtype": "Link", "options": "Vendor KYC", "width": 130},
		{"label": _("Vendor Name"), "fieldname": "firm_name", "fieldtype": "Data", "width": 180},
		{"label": _("KYC Status"), "fieldname": "kyc_status", "fieldtype": "Data", "width": 100},
		{"label": _("Background Check"), "fieldname": "background_check_status", "fieldtype": "Data", "width": 130},
		{"label": _("Compliance Audit"), "fieldname": "compliance_audit_status", "fieldtype": "Data", "width": 130},
		{"label": _("Sampling Evaluation"), "fieldname": "sampling_evaluation_status", "fieldtype": "Data", "width": 140},
		{"label": _("Sign Off"), "fieldname": "sign_off_status", "fieldtype": "Data", "width": 100},
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 150},
	]


def get_kyc_data() -> list[dict]:
	# Each stage's own latest (by creation) record for the KYC — a vendor
	# who redid a stage shouldn't show stale data from an earlier attempt.
	rows = frappe.db.sql(
		"""
		select
			vk.name as kyc,
			vk.firm_name,
			vk.status as kyc_status,
			vk.supplier as vendor,
			(
				select bc.outcome
				from `tabVendor Background Check` bc
				where bc.kyc = vk.name
				order by bc.creation desc limit 1
			) as background_check_status,
			(
				select ca.outcome
				from `tabVendor Compliance Audit` ca
				where ca.kyc = vk.name
				order by ca.creation desc limit 1
			) as compliance_audit_status,
			(
				select se.evaluation_outcome
				from `tabVendor Sampling Evaluation` se
				where se.kyc = vk.name
				order by se.creation desc limit 1
			) as sampling_evaluation_status,
			(
				select case when so.docstatus = 1 then
					(case when so.sign_off_failed then 'Failed' else 'Signed' end)
				else 'Draft' end
				from `tabVendor Sign Off` so
				where so.kyc = vk.name
				order by so.creation desc limit 1
			) as sign_off_status
		from `tabVendor KYC` vk
		order by vk.creation desc
		""",
		as_dict=True,
	)

	_fill_not_started(rows)
	return rows


def get_request_columns() -> list[dict]:
	return [
		{"label": _("Onboarding Request"), "fieldname": "request", "fieldtype": "Link", "options": "Vendor Onboarding Request", "width": 150},
		{"label": _("Company / Firm Name"), "fieldname": "company_name", "fieldtype": "Data", "width": 180},
		{"label": _("Request Status"), "fieldname": "request_status", "fieldtype": "Data", "width": 100},
		{"label": _("KYC"), "fieldname": "kyc", "fieldtype": "Link", "options": "Vendor KYC", "width": 130},
		{"label": _("KYC Status"), "fieldname": "kyc_status", "fieldtype": "Data", "width": 100},
		{"label": _("Background Check"), "fieldname": "background_check_status", "fieldtype": "Data", "width": 130},
		{"label": _("Compliance Audit"), "fieldname": "compliance_audit_status", "fieldtype": "Data", "width": 130},
		{"label": _("Sampling Evaluation"), "fieldname": "sampling_evaluation_status", "fieldtype": "Data", "width": 140},
		{"label": _("Sign Off"), "fieldname": "sign_off_status", "fieldtype": "Data", "width": 100},
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 150},
	]


def get_request_data() -> list[dict]:
	# Anchored on the Onboarding Request instead of the KYC, so a request
	# that hasn't had KYC started yet still shows up (the KYC-anchored view
	# above only ever sees vendors who've reached KYC). Each request's
	# latest KYC (if any) is resolved once via a scalar subquery and reused
	# for every downstream stage lookup.
	latest_kyc_for_request = (
		"(select vk.name from `tabVendor KYC` vk"
		" where vk.onboarding_request = vor.name order by vk.creation desc limit 1)"
	)

	rows = frappe.db.sql(
		f"""
		select
			vor.name as request,
			vor.company_name,
			case vor.docstatus when 0 then 'Draft' when 1 then 'Submitted' else 'Cancelled' end as request_status,
			{latest_kyc_for_request} as kyc,
			(select vk.status from `tabVendor KYC` vk where vk.name = {latest_kyc_for_request}) as kyc_status,
			(select vk.supplier from `tabVendor KYC` vk where vk.name = {latest_kyc_for_request}) as vendor,
			(
				select bc.outcome
				from `tabVendor Background Check` bc
				where bc.kyc = {latest_kyc_for_request}
				order by bc.creation desc limit 1
			) as background_check_status,
			(
				select ca.outcome
				from `tabVendor Compliance Audit` ca
				where ca.kyc = {latest_kyc_for_request}
				order by ca.creation desc limit 1
			) as compliance_audit_status,
			(
				select se.evaluation_outcome
				from `tabVendor Sampling Evaluation` se
				where se.kyc = {latest_kyc_for_request}
				order by se.creation desc limit 1
			) as sampling_evaluation_status,
			(
				select case when so.docstatus = 1 then
					(case when so.sign_off_failed then 'Failed' else 'Signed' end)
				else 'Draft' end
				from `tabVendor Sign Off` so
				where so.kyc = {latest_kyc_for_request}
				order by so.creation desc limit 1
			) as sign_off_status
		from `tabVendor Onboarding Request` vor
		order by vor.creation desc
		""",
		as_dict=True,
	)

	_fill_not_started(rows)
	return rows


def _fill_not_started(rows):
	for row in rows:
		for field in NOT_STARTED_FIELDS:
			row[field] = row.get(field) or _("Not Started")
