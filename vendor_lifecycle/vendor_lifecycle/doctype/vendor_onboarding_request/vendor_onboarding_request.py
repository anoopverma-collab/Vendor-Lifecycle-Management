# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

# Business Details fields required for every business type, once one is picked.
COMMON_BUSINESS_DETAIL_FIELDS = ["estimated_monthly_capacity", "specialization", "years_in_business", "team_size"]

# Extra fields required only for the matching business type.
TYPE_SPECIFIC_MANDATORY_FIELDS = {
	"Manufacturer": ["equipment_count"],
	"Trader": ["brands_distributed", "minimum_order_quantity"],
	"Service Provider": ["service_types_offered", "turnaround_time"],
	"Contractor / Job Worker": ["scope_of_work", "equipment_provided_by"],
	"Logistics / Transporter": ["fleet_size", "coverage_area"],
	"Freelancer / Consultant": ["areas_of_expertise", "availability_hours_per_week"],
	"Raw Material Supplier": ["materials_supplied", "certification_standards"],
	"Equipment / Machinery Supplier": ["equipment_types"],
	"Technology / Software Vendor": ["product_or_platform_name"],
	"Facility / Maintenance Services": ["services_covered"],
	"Other": ["other_business_type"],
}

# Extra fields required only for the matching "How did you hear about us?" answer.
REFERRAL_SPECIFIC_MANDATORY_FIELDS = {
	"Other": ["referral_source_other"],
	"Referral from Existing Vendor": ["existing_vendor_name"],
}


class VendorOnboardingRequest(Document):
	def before_insert(self):
		# Always computed fresh — never trust a caller-supplied value, and
		# don't rely on the field's own default, which would run before this
		# and mask an internally-created request as "Web Form".
		self.source = "Web Form" if frappe.session.user == "Guest" else "Internal"

	def validate(self):
		self._enforce_lock_after_processing()

		# mandatory_depends_on is desk-UI-only in this Frappe version — it
		# never blocks a save via API or web form, so every conditional
		# "mandatory" field in this doctype must also be checked here.
		self._throw_if_missing(REFERRAL_SPECIFIC_MANDATORY_FIELDS.get(self.referral_source, []))

		if self.business_type:
			required = COMMON_BUSINESS_DETAIL_FIELDS + TYPE_SPECIFIC_MANDATORY_FIELDS.get(self.business_type, [])
			self._throw_if_missing(required)

		self._check_duplicate()

	def _enforce_lock_after_processing(self):
		# Once a request has moved past Draft (i.e. a Vendor KYC now exists
		# for it — see VendorKYC.after_insert), it's locked — the only writes
		# allowed from here on are the internal ones made with
		# self.flags.ignore_lock set, or an edit by whoever holds the
		# override role set in Settings.
		if self.is_new() or self.flags.get("ignore_lock"):
			return

		before = self.get_doc_before_save()
		if not before or before.status == "Draft":
			return

		override_role = frappe.db.get_single_value("Vendor Lifecycle Settings", "request_edit_override_role") or "System Manager"
		if override_role in frappe.get_roles():
			return

		frappe.throw(
			frappe._(
				"This request has already been processed and can no longer be edited. Contact a {0} if a correction is needed."
			).format(frappe.bold(override_role)),
			frappe.PermissionError,
		)

	def _check_duplicate(self):
		handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_request_handling") or "Warn"
		if handling == "No Check":
			return

		duplicate = frappe.db.get_value(
			"Vendor Onboarding Request",
			{"name": ["!=", self.name or ""], "email": self.email},
			"name",
		) or frappe.db.get_value(
			"Vendor Onboarding Request",
			{"name": ["!=", self.name or ""], "contact_number": self.contact_number},
			"name",
		)
		if not duplicate:
			return

		message = frappe._(
			"Another Onboarding Request ({0}) already exists with the same email or contact number."
		).format(frappe.bold(duplicate))

		if handling == "Block":
			frappe.throw(message)
		else:
			frappe.msgprint(message, title=frappe._("Duplicate Onboarding Request"), indicator="orange")

	def _throw_if_missing(self, fieldnames):
		missing = [f for f in fieldnames if not self.get(f)]
		if missing:
			labels = [frappe.bold(self.meta.get_label(f)) for f in missing]
			frappe.throw(
				frappe._("Please fill in the following: {0}").format(", ".join(labels)),
				frappe.MandatoryError,
			)

	@frappe.whitelist()
	def accept(self):
		"""Internal review decision: this request is genuinely a vendor worth
		pursuing. Reversible — calling this after a Reject flips it back.
		Only while no Vendor KYC has been started yet; see _enforce_review_decision_open()."""
		if self.status == "Accepted":
			frappe.throw(frappe._("This request has already been accepted."))
		self._enforce_review_decision_open()
		self.flags.ignore_lock = True
		self.status = "Accepted"
		self.reviewed_by = frappe.session.user
		self.save()

	@frappe.whitelist()
	def reject(self):
		"""Internal review decision: this request should not be pursued.
		Reversible — calling Accept afterward flips it back. Only while no
		Vendor KYC has been started yet; see _enforce_review_decision_open()."""
		if self.status == "Rejected":
			frappe.throw(frappe._("This request has already been rejected."))
		self._enforce_review_decision_open()
		self.flags.ignore_lock = True
		self.status = "Rejected"
		self.reviewed_by = frappe.session.user
		self.save()

	def _enforce_review_decision_open(self):
		# Accept/Reject can be flipped back and forth freely, but only until
		# real downstream work exists — once a Vendor KYC has been started
		# for this request (whether it's still a Draft or already
		# submitted), the decision is locked in, so a KYC never ends up
		# hanging off a request that got un-accepted out from under it.
		if frappe.db.exists("Vendor KYC", {"onboarding_request": self.name}):
			frappe.throw(
				frappe._(
					"This request already has a Vendor KYC and can no longer be re-reviewed."
				)
			)

	@frappe.whitelist()
	def start_kyc(self):
		"""Internal review step: return pre-fill values for a new Vendor KYC.

		Nothing is created or saved here — the reviewer gets a fresh, unsaved
		Vendor KYC form pre-filled with this data, and decides for themselves
		when (and whether) to save it."""
		if self.status != "Accepted":
			frappe.throw(frappe._("This request must be Accepted before starting KYC."))

		if frappe.db.exists("Vendor KYC", {"onboarding_request": self.name}):
			handling = frappe.db.get_single_value("Vendor Lifecycle Settings", "duplicate_kyc_handling") or "Stop"
			if handling == "Stop":
				frappe.throw(frappe._("A Vendor KYC already exists for this request."))
			# Warn/Ignore: Settings allows more than one KYC per request — let
			# this proceed; the new Vendor KYC's own validate() surfaces the
			# duplicate warning (or stays silent, for Ignore).

		values = {
			"onboarding_request": self.name,
			"firm_name": self.company_name,
			"business_type": self.business_type,
			"contact_person_name": self.contact_person,
			"contact_person_number": self.contact_number,
			"official_email": self.email,
			"firm_address": self.business_address,
			"city": self.city,
			"state": self.state,
			"country": self.country,
			"pincode": self.pincode,
			"tax_id": self.tax_id,
			"gstin_uin": self.gstin_uin,
		}
		# Covers "Other" -> other_business_type too, via TYPE_SPECIFIC_MANDATORY_FIELDS.
		for fieldname in COMMON_BUSINESS_DETAIL_FIELDS + TYPE_SPECIFIC_MANDATORY_FIELDS.get(self.business_type, []):
			values[fieldname] = self.get(fieldname)

		return values

	@frappe.whitelist()
	def get_pipeline_progress(self):
		"""Where this vendor currently stands across KYC and the four stages
		after it — computed fresh from whatever records exist right now,
		since stages don't have to happen in a fixed order unless Settings
		says so (see stage_sequencing.py)."""
		if self.status not in ("Accepted", "Rejected"):
			return []

		progress = [{"label": "KYC", "state": self._stage_status("Vendor KYC", {"onboarding_request": self.name})}]

		kyc_names = frappe.get_all("Vendor KYC", filters={"onboarding_request": self.name}, pluck="name")
		for doctype, label in (
			("Vendor Compliance Audit", "Compliance Audit"),
			("Vendor Sampling Evaluation", "Sampling Evaluation"),
			("Vendor Reference Check", "Reference Check"),
			("Vendor Sign Off", "Sign Off"),
		):
			state = self._stage_status(doctype, {"kyc": ["in", kyc_names]}) if kyc_names else "Not Started"
			progress.append({"label": label, "state": state})

		return progress

	def _stage_status(self, doctype, filters):
		if frappe.db.exists(doctype, {**filters, "docstatus": 1}):
			return "Completed"
		if frappe.db.exists(doctype, filters):
			return "In Progress"
		return "Not Started"
