# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe


def _is_disabled_supplier(supplier):
	return bool(supplier) and bool(frappe.db.get_value("Supplier", supplier, "disabled"))


def block_disabled_supplier_on_order(doc, method=None):
	"""Purchase Order / Request for Quotation: a disabled vendor cannot be
	ordered from at all — hard block, per the deboarding enforcement rule."""
	if doc.doctype == "Purchase Order":
		if _is_disabled_supplier(doc.supplier):
			_throw_blocked(doc.supplier)
	elif doc.doctype == "Request for Quotation":
		for row in doc.suppliers:
			if _is_disabled_supplier(row.supplier):
				_throw_blocked(row.supplier)


def _throw_blocked(supplier):
	frappe.throw(
		frappe._("Supplier {0} is disabled and cannot be used on new orders.").format(frappe.bold(supplier))
	)


def warn_disabled_supplier_on_transaction(doc, method=None):
	"""Purchase Invoice / Purchase Receipt / Journal Entry / Payment Entry:
	a disabled vendor can still be referenced to close out existing business
	(settling dues, credit notes, etc.) — warn, don't block."""
	if doc.doctype in ("Purchase Invoice", "Purchase Receipt"):
		if _is_disabled_supplier(doc.supplier):
			_warn(doc.supplier)
	elif doc.doctype == "Payment Entry":
		if doc.party_type == "Supplier" and _is_disabled_supplier(doc.party):
			_warn(doc.party)
	elif doc.doctype == "Journal Entry":
		for row in doc.accounts:
			if row.party_type == "Supplier" and _is_disabled_supplier(row.party):
				_warn(row.party)
				return


def _warn(supplier):
	frappe.msgprint(
		frappe._("Supplier {0} is disabled.").format(frappe.bold(supplier)),
		title=frappe._("Disabled Supplier"),
		indicator="orange",
	)
