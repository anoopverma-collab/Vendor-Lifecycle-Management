# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns() -> list[dict]:
	return [
		{"label": _("Request"), "fieldname": "request", "fieldtype": "Link", "options": "Vendor Deboarding Request", "width": 130},
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Vendor Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180},
		{"label": _("Request Status"), "fieldname": "request_status", "fieldtype": "Data", "width": 100},
		{"label": _("Request Date"), "fieldname": "request_date", "fieldtype": "Date", "width": 100},
		{"label": _("Checklist"), "fieldname": "checklist", "fieldtype": "Link", "options": "Vendor Deboarding Checklist", "width": 130},
		{"label": _("Checklist Status"), "fieldname": "checklist_status", "fieldtype": "Data", "width": 110},
		{"label": _("Clearance Received"), "fieldname": "clearance_received", "fieldtype": "Data", "width": 120},
		{"label": _("Temporarily Enabled"), "fieldname": "temporarily_enabled", "fieldtype": "Data", "width": 110},
		{"label": _("Supplier Disabled"), "fieldname": "supplier_disabled", "fieldtype": "Data", "width": 110},
	]


def get_data() -> list[dict]:
	rows = frappe.db.sql(
		"""
		select
			vdr.name as request,
			vdr.vendor,
			s.supplier_name,
			vdr.status as request_status,
			vdr.request_date,
			vdc.name as checklist,
			vdc.docstatus as checklist_docstatus,
			vdc.clearance_attachment,
			vdc.signed_clearance_certificate,
			vdc.no_clearance_certificate,
			vdc.is_temporarily_enabled,
			s.disabled as supplier_disabled
		from `tabVendor Deboarding Request` vdr
		left join `tabVendor Deboarding Checklist` vdc on vdc.deboarding_request = vdr.name and vdc.docstatus != 2
		left join `tabSupplier` s on s.name = vdr.vendor
		order by vdr.creation desc
		""",
		as_dict=True,
	)

	checklist_docstatus_label = {0: _("Draft"), 1: _("Submitted")}

	for row in rows:
		row.checklist_status = checklist_docstatus_label.get(row.checklist_docstatus, _("Not Created"))

		if row.no_clearance_certificate:
			row.clearance_received = _("N/A")
		elif row.signed_clearance_certificate:
			row.clearance_received = _("Yes")
		elif row.clearance_attachment:
			row.clearance_received = _("Awaiting Signature")
		else:
			row.clearance_received = _("No")

		row.temporarily_enabled = _("Yes") if row.is_temporarily_enabled else _("No")
		row.supplier_disabled = _("Yes") if row.supplier_disabled else _("No")

		for field in ("checklist_docstatus", "clearance_attachment", "signed_clearance_certificate", "no_clearance_certificate", "is_temporarily_enabled"):
			row.pop(field, None)

	return rows
