# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED = "KYC Verified"

# Every doctype that can be configured in Settings as the point where the
# Supplier gets created, other than Vendor KYC itself.
SIBLING_DOCTYPES = [
	"Vendor Compliance Audit",
	"Vendor Sampling Evaluation",
	"Vendor Reference Check",
	"Vendor Sign Off",
]


def sync_vendor_field(doc):
	"""Backfill `vendor` from the linked KYC's Supplier, once one exists.
	Call from validate() on any of SIBLING_DOCTYPES — a no-op once vendor is
	already set, and a no-op if the Supplier hasn't been created yet (its
	creation may be deferred to a later stage, per Settings)."""
	if doc.vendor or not doc.kyc:
		return
	supplier = frappe.db.get_value("Vendor KYC", doc.kyc, "supplier")
	if supplier:
		doc.vendor = supplier


def maybe_create_vendor(doc):
	"""Call from on_submit of Vendor KYC or any of SIBLING_DOCTYPES. Creates
	the Supplier only if `doc.doctype` is the stage currently configured in
	Settings as the Supplier-creation trigger, and none exists yet — then
	backfills `vendor` on this doc and any sibling stage documents already
	created for the same KYC. Returns the Supplier doc, or None if this call
	wasn't the configured trigger or a Supplier already existed."""
	trigger = frappe.db.get_single_value("Vendor Lifecycle Settings", "create_vendor_at") or "Vendor KYC"
	if doc.doctype != trigger:
		return None

	kyc = doc if doc.doctype == "Vendor KYC" else frappe.get_doc("Vendor KYC", doc.kyc)
	if kyc.supplier:
		return None

	supplier = _create_or_update_supplier(kyc)

	if doc.doctype != "Vendor KYC":
		doc.db_set("vendor", supplier.name)

	_backfill_sibling_vendor_fields(kyc.name, supplier.name, skip_doctype=doc.doctype, skip_name=doc.name)

	return supplier


# Maps a Vendor KYC field to the Supplier field it fills in. Used both to
# copy the value across below, and (in vendor_kyc.py) to check — before a
# Vendor KYC can be submitted — whether a field Supplier's own doctype
# marks as mandatory has actually been filled in here. That means if
# ERPNext or a site customization ever makes one of these fields required
# on Supplier, Vendor KYC starts enforcing it automatically, with no code
# change needed. Only covers fields Vendor KYC actually has — it can't
# enforce mandatory-ness for a Supplier field with no KYC equivalent.
SUPPLIER_FIELD_MAP = {
	"supplier_group": "supplier_group",
	"billing_currency": "default_currency",
	"payment_terms_template": "payment_terms",
	"tax_withholding_category": "tax_withholding_category",
}


def _create_or_update_supplier(kyc):
	if kyc.supplier:
		supplier = frappe.get_doc("Supplier", kyc.supplier)
		if kyc.supplier_name_override:
			supplier.supplier_name = kyc.supplier_name_override
	else:
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = kyc.supplier_name_override or kyc.firm_name
		supplier.supplier_type = "Company"

	for kyc_fieldname, supplier_fieldname in SUPPLIER_FIELD_MAP.items():
		if kyc.get(kyc_fieldname):
			supplier.set(supplier_fieldname, kyc.get(kyc_fieldname))

	supplier.vendor_lifecycle_status = VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED
	supplier.on_hold = 1
	supplier.hold_type = "All"

	is_new_supplier = supplier.is_new()
	if is_new_supplier:
		supplier.insert(ignore_permissions=True)
	else:
		supplier.save(ignore_permissions=True)

	kyc.db_set("supplier", supplier.name)

	if is_new_supplier:
		_create_addresses_for_supplier(kyc, supplier)

	return supplier


def _create_addresses_for_supplier(kyc, supplier):
	"""Creates one Address record if the reviewer confirmed the GSTIN address
	matches the one filled in manually, or both as separate records if they
	said the two differ. A no-op for whichever side has nothing to create
	from — e.g. a vendor outside India never has a GSTIN address at all."""
	if kyc.is_address_same_as_gstin:
		if kyc.gstin_address_line and kyc.gstin_city:
			_make_address(
				supplier, kyc.address_type or "Billing", kyc.gstin_address_line, kyc.gstin_city, kyc.gstin_state,
				kyc.gstin_pincode, "India",
			)
		return

	if kyc.firm_address and kyc.city:
		_make_address(
			supplier, kyc.address_type or "Billing", kyc.firm_address, kyc.city, kyc.state, kyc.pincode,
			kyc.country or "India",
		)

	if kyc.gstin_address_line and kyc.gstin_city:
		_make_address(
			supplier, "Billing", kyc.gstin_address_line, kyc.gstin_city, kyc.gstin_state, kyc.gstin_pincode, "India"
		)


def _make_address(supplier, address_type, address_line1, city, state, pincode, country):
	address = frappe.new_doc("Address")
	address.address_title = supplier.supplier_name
	address.address_type = address_type
	address.address_line1 = address_line1
	address.city = city
	address.state = state
	address.pincode = pincode
	address.country = country
	address.append("links", {"link_doctype": "Supplier", "link_name": supplier.name})
	address.insert(ignore_permissions=True)
	return address


def _backfill_sibling_vendor_fields(kyc_name, supplier_name, skip_doctype=None, skip_name=None):
	for doctype in SIBLING_DOCTYPES:
		filters = {"kyc": kyc_name, "vendor": ["in", ("", None)]}
		for name in frappe.get_all(doctype, filters=filters, pluck="name"):
			if doctype == skip_doctype and name == skip_name:
				continue
			frappe.db.set_value(doctype, name, "vendor", supplier_name)
