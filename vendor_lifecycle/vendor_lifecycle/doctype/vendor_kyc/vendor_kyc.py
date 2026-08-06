# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.vendor_creation import SUPPLIER_FIELD_MAP, maybe_create_vendor

PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# Used only as a fallback when india_compliance isn't installed — format-only,
# no checksum. india_compliance's own validate_gstin() is preferred whenever
# it's available, since it also validates the check digit.
GSTIN_FORMAT_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

BANK_DETAIL_FIELDS = ["bank", "bank_account_no", "ifsc_branch_code", "bank_account_name"]
ADDRESS_DETAIL_FIELDS = ["firm_address", "city", "state", "country", "pincode"]
# A field literally named "country" silently inherits the site's global
# default (frappe.db.get_default("country")) on every new document, so it's
# excluded from the set that *triggers* the group-mandatory rule below —
# otherwise the address section would look "started" on every single new
# Vendor KYC, whether or not anyone actually touched it.
ADDRESS_TRIGGER_FIELDS = ["firm_address", "city", "state", "pincode"]


class VendorKYC(Document):
	def validate(self):
		self._enforce_creation_source()
		self._enforce_onboarding_request_mandatory()
		self._enforce_request_accepted()
		self._enforce_onboarding_request_immutable()
		self._check_duplicate_kyc_for_request()
		self._validate_pan_gstin()
		self._enforce_group_mandatory()

	def _enforce_creation_source(self):
		# onboarding_request being set is itself sufficient proof this came
		# from a real request — Start KYC now hands the reviewer a pre-filled,
		# unsaved form (see Vendor Onboarding Request's start_kyc()) rather
		# than inserting server-side, so there's no request/response boundary
		# left to carry an in-memory flag across. The flag is kept only as a
		# convenience for internal/test callers that construct a KYC with no
		# onboarding_request at all.
		if not self.is_new() or self.flags.get("via_onboarding_request") or self.onboarding_request:
			return
		if frappe.db.get_single_value("Vendor Lifecycle Settings", "require_onboarding_request_for_kyc"):
			frappe.throw(
				frappe._(
					'Direct creation of Vendor KYC is disabled. Please use the "Start KYC" button on the relevant Vendor Onboarding Request.'
				),
				frappe.PermissionError,
			)

	def _enforce_onboarding_request_mandatory(self):
		# Onboarding Request is only mandatory when Settings requires it for
		# KYC creation — when that's off, direct creation without any request
		# reference is allowed. The reqd property isn't set statically on the
		# field for this reason; the Desk UI toggles it via the same setting
		# (see the "Vendor KYC Direct Create Notice" client script).
		if not self.is_new() or self.flags.get("via_onboarding_request"):
			return
		if frappe.db.get_single_value("Vendor Lifecycle Settings", "require_onboarding_request_for_kyc") and not self.onboarding_request:
			frappe.throw(frappe._("Onboarding Request is mandatory."), frappe.MandatoryError)

	def _enforce_request_accepted(self):
		if not self.is_new() or not self.onboarding_request:
			return
		request_status = frappe.db.get_value("Vendor Onboarding Request", self.onboarding_request, "status")
		if request_status != "Accepted":
			frappe.throw(
				frappe._("The linked Onboarding Request must be Accepted before starting a Vendor KYC.")
			)

	def _enforce_onboarding_request_immutable(self):
		if self.is_new():
			return
		if self.has_value_changed("onboarding_request"):
			frappe.throw(frappe._("Onboarding Request cannot be changed once a Vendor KYC has been created."))

	def _check_duplicate_kyc_for_request(self):
		if not self.onboarding_request:
			return

		handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_kyc_handling") or "Stop"
		if handling == "Ignore":
			return

		duplicate = frappe.db.get_value(
			"Vendor KYC",
			{"name": ["!=", self.name or ""], "onboarding_request": self.onboarding_request},
			"name",
		)
		if not duplicate:
			return

		message = frappe._("Another Vendor KYC ({0}) already exists for this Onboarding Request.").format(
			frappe.bold(duplicate)
		)

		if handling == "Stop":
			frappe.throw(message)
		else:
			frappe.msgprint(message, title=frappe._("Duplicate Vendor KYC"), indicator="orange")

	def _validate_pan_gstin(self):
		if self.country == "India" and self.tax_id:
			pan = self.tax_id.strip().upper()
			if not PAN_REGEX.match(pan):
				frappe.throw(
					frappe._("{0} is not a valid PAN. Expected format: AAAAA9999A.").format(frappe.bold(self.tax_id))
				)
			self.tax_id = pan

		if self.gstin_uin:
			gstin = self.gstin_uin.strip().upper()
			if "india_compliance" in frappe.get_installed_apps():
				from india_compliance.gst_india.utils import validate_gstin

				validate_gstin(gstin, label="GSTIN")
			elif not GSTIN_FORMAT_REGEX.match(gstin):
				frappe.throw(frappe._("{0} is not a valid GSTIN.").format(frappe.bold(self.gstin_uin)))
			self.gstin_uin = gstin

	def _enforce_group_mandatory(self):
		settings = frappe.get_single("Vendor Lifecycle Settings")

		if settings.require_complete_bank_details:
			self._throw_if_partially_filled(BANK_DETAIL_FIELDS, BANK_DETAIL_FIELDS, frappe._("Bank Details"))

		if settings.require_complete_address_details:
			self._throw_if_partially_filled(ADDRESS_DETAIL_FIELDS, ADDRESS_TRIGGER_FIELDS, frappe._("Address"))

	def _throw_if_partially_filled(self, fieldnames, trigger_fieldnames, group_label):
		if not any(self.get(f) for f in trigger_fieldnames):
			return
		missing = [f for f in fieldnames if not self.get(f)]
		if not missing:
			return
		labels = [frappe.bold(self.meta.get_label(f)) for f in missing]
		frappe.throw(
			frappe._("{0}: once you fill in part of this section, the rest is required too. Missing: {1}").format(
				group_label, ", ".join(labels)
			),
			frappe.MandatoryError,
		)

	@frappe.whitelist()
	def fetch_gstin_details(self):
		"""Look up the GSTIN-registered address via India Compliance, if it's
		installed. Only populates the read-only gstin_* fields for review —
		the reviewer decides whether to use it (via the "Address Same as
		GSTIN" checkbox or by copying it into the address fields above)."""
		if "india_compliance" not in frappe.get_installed_apps():
			frappe.throw(
				frappe._("The India Compliance app is not installed, so GSTIN address lookup isn't available.")
			)
		if not self.gstin_uin:
			frappe.throw(frappe._("Enter a GSTIN first."))

		from india_compliance.gst_india.utils.gstin_info import get_gstin_info

		info = get_gstin_info(self.gstin_uin)
		address = info.get("permanent_address") or {}

		self.gstin_address_line = address.get("address_line1") or ""
		self.gstin_city = address.get("city") or ""
		self.gstin_state = address.get("state") or ""
		self.gstin_pincode = address.get("pincode") or ""

		return {
			"business_name": info.get("business_name"),
			"address_line": self.gstin_address_line,
			"city": self.gstin_city,
			"state": self.gstin_state,
			"pincode": self.gstin_pincode,
		}

	def before_submit(self):
		self._enforce_supplier_mandatory_fields()

	def _enforce_supplier_mandatory_fields(self):
		supplier_meta = frappe.get_meta("Supplier")
		missing_labels = []
		for kyc_fieldname, supplier_fieldname in SUPPLIER_FIELD_MAP.items():
			supplier_field = supplier_meta.get_field(supplier_fieldname)
			if not supplier_field or not supplier_field.reqd:
				continue
			if not self.get(kyc_fieldname):
				missing_labels.append(frappe.bold(self.meta.get_label(kyc_fieldname)))

		if missing_labels:
			frappe.throw(
				frappe._(
					"These fields are mandatory on the Supplier master and must be filled in before submitting: {0}"
				).format(", ".join(missing_labels)),
				frappe.MandatoryError,
			)

	def on_submit(self):
		self.status = "Verified"
		# A no-op unless Settings has "Create Vendor At" pointed at Vendor
		# KYC (the default) — see vendor_creation.py for the other stages
		# this can be deferred to.
		maybe_create_vendor(self)

	def on_cancel(self):
		self.status = "Draft"
