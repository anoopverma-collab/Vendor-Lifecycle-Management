# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from vendor_lifecycle.vendor_lifecycle.doctype.vendor_background_check.vendor_background_check import (
	VENDOR_LIFECYCLE_STATUS_BACKGROUND_VERIFIED,
)
from vendor_lifecycle.vendor_lifecycle.doctype.vendor_compliance_audit.vendor_compliance_audit import (
	VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED,
)
from vendor_lifecycle.vendor_lifecycle.doctype.vendor_sampling_evaluation.vendor_sampling_evaluation import (
	VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED,
)
from vendor_lifecycle.vendor_lifecycle.stage_sequencing import (
	enforce_sequential_creation,
	is_sampling_mandatory,
	require_no_active_document_for_kyc,
	stage_requirement_satisfied,
)
from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
	VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED,
	get_disable_reason_for_supplier,
	mark_vendor_status_in_progress,
	require_vendor_lifecycle_email_account,
	send_vendor_lifecycle_email,
	sync_onboarding_request_field,
	sync_vendor_field,
	vendor_lifecycle_cc_list,
	vendor_lifecycle_emails_enabled,
)

VENDOR_LIFECYCLE_STATUS_ACTIVE = "Active"
VENDOR_LIFECYCLE_STATUS_SIGN_OFF_IN_PROGRESS = "Sign Off In Progress"

# Hardcoded, by explicit product decision — no longer Settings-configurable
# Link fields (see install.py's backfill_default_signoff_*_email_template
# for where these are seeded).
DEFAULT_SIGNOFF_EMAIL_TEMPLATE = "Vendor Sign-off Request"
DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE = "Vendor Sign-off Document Received"
DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE = "Vendor Sign-off Passed"
DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE = "Vendor Sign-off Failed"
DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE = "Vendor Sign-off Follow-up"

# A draft Sign-off still missing a signed document gets nudged again
# after this many days — same cadence as the Deboarding Checklist's own
# clearance-certificate follow-up.
SIGNOFF_FOLLOWUP_DAYS = 7


class VendorSignOff(Document):
	def validate(self):
		sync_vendor_field(self)
		sync_onboarding_request_field(self)
		enforce_sequential_creation(self)
		mark_vendor_status_in_progress(self, VENDOR_LIFECYCLE_STATUS_SIGN_OFF_IN_PROGRESS)
		self._warn_if_no_default_company()
		self._require_failure_reason_if_failed()

	def _require_failure_reason_if_failed(self):
		# failure_reason's own mandatory_depends_on is client-side only —
		# Frappe's server-side _get_missing_mandatory_fields() only ever
		# looks at a field's static reqd:1, never mandatory_depends_on — so
		# this has to be enforced explicitly here, or a direct API/console
		# submit could skip it entirely.
		if self.sign_off_failed and not self.failure_reason:
			frappe.throw(frappe._("Failure Reason is mandatory when Sign Off Failed is checked."))

	def _warn_if_no_default_company(self):
		# A warning, not a block — send_signoff_email() itself is what
		# actually enforces this (a real block makes sense once the user
		# is trying to send, not on every save). This is just an early
		# heads-up. self.company (this document's own override) is
		# checked first — only warn if neither it nor Global Defaults has
		# one.
		if not self._resolve_company():
			frappe.msgprint(
				frappe._(
					"No Company is set on this Sign-off, and no Default Company is set in Global Defaults —"
					" the Sign-off email won't be able to include a GSTIN."
				),
				title=frappe._("No Company Set"),
				indicator="orange",
			)

	def _resolve_company(self):
		return self.company or frappe.defaults.get_global_default("company")

	def before_insert(self):
		require_no_active_document_for_kyc(self)
		# Not currently consumed anywhere (the Web Form that used this was
		# removed — see git history) — kept as a stable per-document secret
		# in case a future vendor-facing mechanism needs one again, so it
		# doesn't have to be reintroduced from scratch.
		self.upload_token = frappe.generate_hash(length=32)

	def before_submit(self):
		if self.sign_off_failed:
			# A failed Sign-off is a terminal outcome, not a normal
			# completion — none of the usual "other stages passed" /
			# "signed document attached" gates apply. failure_reason being
			# mandatory when this is checked is enforced natively by the
			# field's own mandatory_depends_on.
			return
		self._enforce_mandatory_stages()
		self._require_signed_contract()
		self._require_signed_code_of_conduct()

	def _enforce_mandatory_stages(self):
		# Independent of enforce_sequential_creation() (which only checks
		# the NEAREST mandatory stage, and only at creation time) — this is
		# Sign-off's own final gate, checked again at submit, that every
		# stage Settings actually requires has genuinely succeeded, even
		# when "Enforce Sequential Stages" is off. Uses the same
		# stage_requirement_satisfied() stage_sequencing.py uses for
		# _nearest_requirement, so a submitted-but-Failed/Rejected stage
		# only counts as satisfied if it was explicitly Force Overridden —
		# never drifts from that rule.
		settings = frappe.get_single("Vendor Lifecycle Settings")
		missing = []

		if settings.audit_mandatory and not stage_requirement_satisfied(
			"Vendor Compliance Audit", {"kyc": self.kyc, "docstatus": 1, "outcome": "Passed"}
		):
			missing.append(frappe._("a passed Compliance Audit"))

		business_type = frappe.db.get_value("Vendor KYC", self.kyc, "business_type")
		if is_sampling_mandatory(business_type, settings) and not stage_requirement_satisfied(
			"Vendor Sampling Evaluation", {"kyc": self.kyc, "docstatus": 1, "evaluation_outcome": "Approved"}
		):
			missing.append(frappe._("an approved Sampling Evaluation"))

		if settings.background_check_mandatory and not stage_requirement_satisfied(
			"Vendor Background Check", {"kyc": self.kyc, "docstatus": 1, "overall_status": "Passed"}
		):
			missing.append(frappe._("a passed Background Check"))

		if missing:
			frappe.throw(frappe._("Sign-off is blocked until this vendor has: {0}.").format(", ".join(missing)))

	def _require_signed_contract(self):
		# Deferred: a pluggable e-sign provider integration (DocuSign,
		# Digio, etc.) was designed and built here, but removed — only a
		# manual attach-and-confirm flow is needed for now. Revisit this
		# as a real integration if/when that becomes a priority.
		if not self.signed_contract:
			frappe.throw(frappe._("Attach the Signed Contract before submitting."))

	def _require_signed_code_of_conduct(self):
		# Same "unsigned before send, signed before submit" pairing as
		# Contract/Signed Contract above — but only when Code of Conduct
		# was actually acknowledged, since that whole section is optional.
		if self.code_of_conduct_acknowledged and not self.code_of_conduct_document:
			frappe.throw(frappe._("Attach the signed Code of Conduct before submitting."))

	@frappe.whitelist()
	def check_submit_readiness(self):
		# Same pattern as the other stage doctypes' check_submit_readiness
		# — dry-runs before_submit()'s own preconditions so the client can
		# surface a real blocking problem via a clean dialog instead of a
		# raw validation error on the actual submit attempt.
		# sign_off_failed is returned too so the client can show the same
		# "this disables the vendor" confirmation Compliance Audit /
		# Sampling Evaluation / Background Check show for their own
		# negative outcomes.
		structural_error = None
		if not self.sign_off_failed:
			try:
				self._enforce_mandatory_stages()
				self._require_signed_contract()
				self._require_signed_code_of_conduct()
			except frappe.ValidationError as e:
				structural_error = str(e)
				frappe.clear_last_message()

		return {"structural_error": structural_error, "sign_off_failed": bool(self.sign_off_failed)}

	@frappe.whitelist()
	def send_signoff_email(self):
		"""Emails the vendor using the org's configured Email Template —
		draft-only. Sends one email per document (Contract, and Code of
		Conduct if acknowledged) instead of a single combined email, so
		each outbound Communication can be tagged with exactly which
		document it concerns (vendor_lifecycle_signoff_document_type on
		Communication). A vendor reply threads back to its own outbound
		email (Frappe matches the In-Reply-To header), which is how
		signoff_reply.py later resolves which Attach field an inbound
		attachment belongs to.

		Interim design, per explicit product decision: this only works
		cleanly because each email carries exactly one attachment. It does
		not handle a single combined email covering both documents — a
		better (multi-attachment-aware) solution is still needed.
		"""
		if self.docstatus != 0:
			frappe.throw(frappe._("The Sign-off email can only be sent while this document is a draft."))
		if self.sign_off_failed:
			frappe.throw(frappe._("The Sign-off email cannot be sent once this Sign-off is marked as failed."))
		if not self.supplier_email:
			frappe.throw(frappe._("No Supplier Email is set on this Vendor KYC — nothing to send to."))
		if not self.contract:
			frappe.throw(frappe._("Attach the Contract before sending the email."))
		if self.code_of_conduct_acknowledged and not self.code_of_conduct:
			frappe.throw(frappe._("Attach the Code of Conduct before sending the email."))
		if not self._resolve_company():
			frappe.throw(
				frappe._("Set a Company on this Sign-off, or a Default Company in Global Defaults, before sending the email.")
			)

		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			frappe.throw(frappe._('"Use Emails" is off in Vendor Lifecycle Settings — enable it first.'))
		if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_EMAIL_TEMPLATE):
			frappe.throw(
				frappe._("The {0} Email Template is missing — recreate it before sending.").format(
					frappe.bold(DEFAULT_SIGNOFF_EMAIL_TEMPLATE)
				)
			)
		email_account = require_vendor_lifecycle_email_account(settings)
		template = frappe.get_doc("Email Template", DEFAULT_SIGNOFF_EMAIL_TEMPLATE)

		cc = vendor_lifecycle_cc_list(settings)

		recipients = [self.supplier_email]
		if self.additional_email and self.additional_email not in recipients:
			recipients.append(self.additional_email)

		document_jobs = [("Contract", "contract")]
		if self.code_of_conduct_acknowledged:
			document_jobs.append(("Code of Conduct", "code_of_conduct"))

		sent_documents = []
		for document_type, fieldname in document_jobs:
			self._send_document_email(document_type, fieldname, template, cc, recipients, email_account)
			sent_documents.append(document_type)

		# Baseline for the follow-up cadence (see send_signoff_followup() /
		# tasks.send_signoff_followups) — counts from whichever email was
		# sent most recently, initial request or a follow-up.
		self.db_set("last_reminder_sent", frappe.utils.today())

		return {
			"sent_to": recipients,
			"cc": cc,
			"email_account": email_account.name,
			"documents": sent_documents,
		}

	def _missing_documents(self):
		missing = []
		if not self.signed_contract:
			missing.append("Contract")
		if self.code_of_conduct_acknowledged and not self.code_of_conduct_document:
			missing.append("Code of Conduct")
		return missing

	def _send_signoff_followup_email(self):
		"""Core body for the "not yet signed" follow-up — never raises on
		a missing setting/template/recipient, just returns False, so the
		scheduled job (tasks.send_signoff_followups) can silently skip
		while the manual button below still surfaces a clear error to
		whoever clicked it."""
		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return False
		if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE):
			return False
		if not self.supplier_email:
			return False

		recipients = [self.supplier_email]
		if self.additional_email and self.additional_email not in recipients:
			recipients.append(self.additional_email)

		context = self._build_email_context()
		context["missing_documents"] = ", ".join(self._missing_documents())
		send_vendor_lifecycle_email(
			doctype=self.doctype,
			name=self.name,
			template_name=DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE,
			context=context,
			recipients=recipients,
		)
		self.db_set("last_reminder_sent", frappe.utils.today())
		return True

	@frappe.whitelist()
	def send_signoff_followup(self):
		if self.docstatus != 0:
			frappe.throw(frappe._("The follow-up can only be sent while this Sign-off is a draft."))
		if self.sign_off_failed:
			frappe.throw(frappe._("The follow-up cannot be sent once this Sign-off is marked as failed."))
		if not self._missing_documents():
			frappe.throw(frappe._("Every required document has already been signed and attached."))
		if not self.last_reminder_sent:
			frappe.throw(frappe._("Send the Sign-off email first."))
		if not self._send_signoff_followup_email():
			frappe.throw(
				frappe._(
					"Could not send — check Vendor Lifecycle Settings (emails enabled, the Email Template, and"
					" the Supplier Email)."
				)
			)
		return {"sent": True}

	def _send_document_email(self, document_type, fieldname, template, cc, recipients, email_account):
		context = self._build_email_context()
		context["document_type"] = document_type
		subject = template.get_formatted_subject(context)
		message = template.get_formatted_response(context)

		file_name = self._get_attachment_file(fieldname)
		attachments = [file_name] if file_name else []

		# frappe.sendmail() alone only queues an Email Queue entry — it does
		# NOT create a Communication record, so nothing would show up in
		# this document's own timeline. communication.email.make() is what
		# the Desk "New Email" button itself uses: it creates the
		# Communication (linked via reference_doctype/reference_name).
		# send_email=False here — email_account has to be forced onto the
		# Communication itself (below) before the actual send, since
		# make()/_make() has no way to pass a specific account through, and
		# Communication.get_outgoing_email_account() otherwise falls back to
		# whatever "sender" happens to match, or the site's own default —
		# exactly the fallback the user explicitly doesn't want.
		#
		# sender is passed explicitly for the same reason: make() defaults
		# it to the current user (frappe.session.user), and Communication's
		# own mail_sender() prefers a non-blank self.sender over the forced
		# email_account — leaving sender unset here sent the email as
		# whichever user clicked "Send Email" (e.g. Administrator ->
		# admin@example.com) instead of the configured Sign-off Email
		# Account, even though the account itself was used to relay it.
		from frappe.core.doctype.communication.email import make as make_communication

		result = make_communication(
			doctype=self.doctype,
			name=self.name,
			content=message,
			subject=subject,
			sender=email_account.email_id,
			recipients=", ".join(recipients),
			cc=", ".join(cc) if cc else None,
			attachments=attachments,
			send_email=False,
		)

		comm = frappe.get_doc("Communication", result["name"])
		comm.db_set("vendor_lifecycle_signoff_document_type", document_type, update_modified=False)
		comm.db_set("email_account", email_account.name, update_modified=False)
		# now=False (the default) — queues for the scheduler to actually
		# deliver, rather than blocking this request on a live SMTP
		# round-trip. The Communication record (and this success response)
		# are immediate either way.
		comm.send_email()

	def _get_attachment_file(self, fieldname):
		return frappe.db.get_value(
			"File",
			{"attached_to_doctype": self.doctype, "attached_to_name": self.name, "attached_to_field": fieldname},
			"name",
		)

	def _build_email_context(self):
		# Always pass every identifier a template could plausibly want —
		# company/GSTIN were already here, PAN (both the vendor's own and
		# the sending company's) is exactly the same kind of variable and
		# easy to miss unless deliberately kept in this one place.
		company = self._resolve_company()
		kyc_details = (
			frappe.db.get_value("Vendor KYC", self.kyc, ["firm_name", "gstin_uin", "pan_card"], as_dict=True) or {}
		)
		company_details = (
			frappe.db.get_value("Company", company, ["gstin", "pan"], as_dict=True) if company else {}
		) or {}
		return {
			"kyc": self.kyc,
			"onboarding_request": self.onboarding_request,
			"notes": self.notes,
			"firm_name": kyc_details.get("firm_name"),
			"vendor_gstin": kyc_details.get("gstin_uin"),
			"vendor_pan": kyc_details.get("pan_card"),
			"contact_person_name": self.contact_person_name,
			"company_name": company,
			"company_gstin": company_details.get("gstin"),
			"company_pan": company_details.get("pan"),
			"upload_link": self._build_upload_link(),
			"sign_off_name": self.name,
			"sign_off_link": frappe.utils.get_url_to_form(self.doctype, self.name),
		}

	def _build_upload_link(self):
		# The page this used to point to (a Web Form) was removed — see git
		# history. Kept as a method so the token+link-building mechanism
		# doesn't have to be rebuilt from scratch once a replacement exists;
		# the default Email Template no longer references {{ upload_link }},
		# so this currently has no visible effect until a real destination
		# page exists again and a template is updated to use it.
		return "{0}/vendor-signoff-upload?sign_off={1}&token={2}".format(
			frappe.utils.get_url(), self.name, self.upload_token
		)

	def on_update(self):
		# Covers a staff member manually attaching the signed file on the
		# Desk form. The other path a document can arrive by — the vendor
		# replying by email — bypasses this (signoff_reply.py sets the
		# field via db_set, which skips doc_events) and calls
		# notify_document_received() directly instead.
		self._notify_if_document_received("signed_contract", "Contract")
		self._notify_if_document_received("code_of_conduct_document", "Code of Conduct")

	def _notify_if_document_received(self, fieldname, document_type):
		if not self.has_value_changed(fieldname) or not self.get(fieldname):
			return
		try:
			self.notify_document_received(document_type)
		except Exception:
			frappe.log_error(
				title="Vendor Sign Off: failed to notify creator of received document",
				message=frappe.get_traceback(),
			)

	def notify_document_received(self, document_type):
		creator_email = self._resolve_owner_email()
		if not creator_email:
			return

		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return
		if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE):
			return
		cc = vendor_lifecycle_cc_list(settings)

		template = frappe.get_doc("Email Template", DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE)
		context = self._build_email_context()
		context["document_type"] = document_type
		subject = template.get_formatted_subject(context)
		message = template.get_formatted_response(context)

		from frappe.core.doctype.communication.email import make as make_communication

		make_communication(
			doctype=self.doctype,
			name=self.name,
			subject=subject,
			content=message,
			recipients=creator_email,
			cc=", ".join(cc) if cc else None,
			send_email=True,
		)

	def _resolve_owner_email(self):
		# Same "Administrator" special-case Frappe itself uses when
		# resolving a sender (see Communication.set_sender_full_name) —
		# self.owner is "Administrator" rather than a real email address
		# when the document was created from the console/scheduler/tests.
		if self.owner == "Administrator":
			return frappe.db.get_value("User", "Administrator", "email")
		return self.owner

	def on_submit(self):
		# The signed-contract/code-of-conduct checks already ran in
		# before_submit() — no need to repeat them here.
		if self.sign_off_failed:
			self._handle_failed_result()
		else:
			self._handle_passed_result()

		self._send_outcome_email()

	def _handle_passed_result(self):
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, {
				"vendor_lifecycle_status": VENDOR_LIFECYCLE_STATUS_ACTIVE,
				"is_frozen": 0,
			})

	def _handle_failed_result(self):
		# Same treatment as a Failed Compliance Audit / Background Check
		# (_handle_failed_result there) — disables the Supplier outright.
		# vendor_lifecycle_status is deliberately left untouched, matching
		# that same convention: it's a progress tracker, not the actual
		# block (disabled is), and Sign-off has no "next stage" to point it
		# at anyway.
		if self.vendor:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 1)

	def _send_outcome_email(self):
		# Must never block the submit transaction itself — a missing
		# template/account is a notification problem, not a reason to
		# refuse recording the actual pass/fail outcome.
		try:
			self._send_outcome_email_unsafe()
		except Exception:
			frappe.log_error(
				title="Vendor Sign Off: failed to send outcome email",
				message=frappe.get_traceback(),
			)

	def _send_outcome_email_unsafe(self):
		settings = frappe.get_single("Vendor Lifecycle Settings")
		if not vendor_lifecycle_emails_enabled(settings):
			return
		template_name = DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE if self.sign_off_failed else DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE
		if not frappe.db.exists("Email Template", template_name):
			return

		recipients = []
		creator_email = self._resolve_owner_email()
		if creator_email:
			recipients.append(creator_email)
		if self.supplier_email and self.supplier_email not in recipients:
			recipients.append(self.supplier_email)
		if self.additional_email and self.additional_email not in recipients:
			recipients.append(self.additional_email)
		if not recipients:
			return

		cc = vendor_lifecycle_cc_list(settings)

		template = frappe.get_doc("Email Template", template_name)
		context = self._build_email_context()
		context["failure_reason"] = self.failure_reason
		subject = template.get_formatted_subject(context)
		message = template.get_formatted_response(context)

		# Best-effort only, unlike send_signoff_email()'s no-fallback
		# requirement — this outcome email must still go out even if the
		# Vendor Lifecycle Email Account is missing or misconfigured.
		account = None
		if settings.vendor_lifecycle_email_account:
			account = frappe.db.get_value(
				"Email Account", settings.vendor_lifecycle_email_account, ["name", "email_id"], as_dict=True
			)

		from frappe.core.doctype.communication.email import make as make_communication

		result = make_communication(
			doctype=self.doctype,
			name=self.name,
			subject=subject,
			content=message,
			sender=account.email_id if account else None,
			recipients=", ".join(recipients),
			cc=", ".join(cc) if cc else None,
			send_email=False,
		)
		comm = frappe.get_doc("Communication", result["name"])
		if account:
			comm.db_set("email_account", account.name, update_modified=False)
		comm.send_email()

	def on_cancel(self):
		if self.sign_off_failed:
			self._revert_disable_if_this_was_the_failed_one()
		else:
			self._revert_vendor_status_to_last_completed_stage()

	def _revert_disable_if_this_was_the_failed_one(self):
		# Broader than Vendor Compliance Audit / Sampling Evaluation's own
		# same-named method (which only check for another failed record of
		# their own doctype) — this checks every disable source
		# (get_disable_reason_for_supplier), since Sign-off is the last
		# stage and can legitimately follow any of the others in a
		# non-sequential setup. Cancelling retracts this Sign-off's own
		# consequence, but only if nothing else is still disabling the
		# Supplier.
		if not self.vendor:
			return
		reason = get_disable_reason_for_supplier(self.vendor)
		if not reason or reason == {"doctype": self.doctype, "name": self.name}:
			frappe.db.set_value("Supplier", self.vendor, "disabled", 0)

	def _revert_vendor_status_to_last_completed_stage(self):
		# Undoes on_submit()'s two side effects — re-freezes the Supplier
		# (matches its state at creation, see vendor_creation.py) and
		# rolls vendor_lifecycle_status back to whichever stage actually
		# succeeded most recently, walking backward the same way
		# _nearest_requirement() does, except this always checks every
		# stage regardless of Settings' mandatory/skip flags — a stage
		# that happened (even an optional one) still reflects real
		# progress that shouldn't be erased just because Sign Off is gone.
		if not self.vendor:
			return

		if frappe.db.exists(
			"Vendor Sampling Evaluation", {"kyc": self.kyc, "docstatus": 1, "evaluation_outcome": "Approved"}
		):
			status = VENDOR_LIFECYCLE_STATUS_SAMPLING_APPROVED
		elif frappe.db.exists("Vendor Compliance Audit", {"kyc": self.kyc, "docstatus": 1, "outcome": "Passed"}):
			status = VENDOR_LIFECYCLE_STATUS_AUDIT_VERIFIED
		elif frappe.db.exists("Vendor Background Check", {"kyc": self.kyc, "docstatus": 1, "overall_status": "Passed"}):
			status = VENDOR_LIFECYCLE_STATUS_BACKGROUND_VERIFIED
		else:
			status = VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED

		frappe.db.set_value("Supplier", self.vendor, {
			"vendor_lifecycle_status": status,
			"is_frozen": 1,
		})
