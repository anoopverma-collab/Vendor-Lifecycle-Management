# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data()
	return columns, data


def get_columns() -> list[dict]:
	return [
		{"label": _("Vendor"), "fieldname": "vendor", "fieldtype": "Link", "options": "Supplier", "width": 200},
		{"label": _("Vendor Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 200},
		{
			"label": _("Latest Compliance Audit"),
			"fieldname": "audit",
			"fieldtype": "Link",
			"options": "Vendor Compliance Audit",
			"width": 180,
		},
		{"label": _("Audit Date"), "fieldname": "audit_date", "fieldtype": "Date", "width": 100},
		{"label": _("Valid Until"), "fieldname": "valid_until", "fieldtype": "Date", "width": 100},
		{"label": _("Days Remaining"), "fieldname": "days_remaining", "fieldtype": "Int", "width": 120},
	]


def get_data() -> list[dict]:
	# Each vendor's own most recent Passed, submitted Compliance Audit — a
	# vendor with an older Passed audit superseded by a newer one shouldn't
	# show up under the older audit's now-irrelevant Valid Until.
	rows = frappe.db.sql(
		"""
		select vca.vendor, s.supplier_name, vca.name as audit, vca.audit_date, vca.valid_until
		from `tabVendor Compliance Audit` vca
		inner join (
			select vendor, max(audit_date) as latest_audit_date
			from `tabVendor Compliance Audit`
			where docstatus = 1 and outcome = 'Passed' and vendor is not null and vendor != ''
			group by vendor
		) latest on latest.vendor = vca.vendor and latest.latest_audit_date = vca.audit_date
		left join `tabSupplier` s on s.name = vca.vendor
		where vca.docstatus = 1 and vca.outcome = 'Passed'
		order by vca.valid_until asc
		""",
		as_dict=True,
	)

	today = getdate(nowdate())
	for row in rows:
		row.days_remaining = (getdate(row.valid_until) - today).days if row.valid_until else None

	return rows
