# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

def _is_globally_mandatory(fieldname):
	"""Simple case: a stage's mandatory-ness is just one flat Settings
	Check field, the same for every vendor regardless of Business Type."""
	def check(business_type, settings):
		return bool(settings.get(fieldname))
	return check


def is_sampling_mandatory(business_type, settings):
	# The Business Type table is the sole source of truth, by explicit
	# product decision — no flat fallback checkbox, and no per-row toggle
	# either: being listed IS being mandatory. A Business Type not in the
	# table at all is simply not mandatory. Public (no leading underscore)
	# since Vendor Sign Off's own mandatory-stage gate reuses this directly.
	if not business_type:
		return False
	return any(row.business_type == business_type for row in settings.sampling_mandatory_overrides)


# Order stages always happen in (see enforce_sequential_creation). The second
# item in each tuple decides whether that stage is skippable — None for
# stages that are never optional (KYC, Sign Off), otherwise a function of
# (business_type, settings) -> bool, called with this KYC's own Business
# Type and the loaded Vendor Lifecycle Settings document.
STAGE_SEQUENCE = [
	("Vendor KYC", None),
	("Vendor Background Check", _is_globally_mandatory("background_check_mandatory")),
	("Vendor Compliance Audit", _is_globally_mandatory("audit_mandatory")),
	("Vendor Sampling Evaluation", is_sampling_mandatory),
	("Vendor Sign Off", None),
]

# These three (and only these three) have a force_overridden Check field and
# a force_override() whitelisted method — a System Manager / Vendor
# Lifecycle Manager can override a genuinely Failed/Rejected result so the
# pipeline can proceed anyway. Vendor KYC and Vendor Sign Off are excluded
# deliberately: KYC has no failing outcome to override, and Sign Off's own
# "Failed" is a terminal decision about the vendor overall, not a mid-
# pipeline gate to unblock.
FORCE_OVERRIDABLE_DOCTYPES = {
	"Vendor Background Check",
	"Vendor Compliance Audit",
	"Vendor Sampling Evaluation",
}

# The stage that normally comes right after each force-overridable one —
# used only for the "you're eligible for the next stage" wording in the
# override-approved email; not a substitute for get_available_stages()'s
# own, Settings-aware notion of what's actually next.
NEXT_STAGE_LABEL = {
	"Vendor Background Check": "Compliance Audit",
	"Vendor Compliance Audit": "Sampling Evaluation",
	"Vendor Sampling Evaluation": "Sign Off",
}


def stage_requirement_satisfied(doctype, filters):
	"""Whether a submitted record of `doctype` matching `filters` (always
	{"kyc": ..., "docstatus": 1, <outcome field>: <passing value>}) exists
	— OR, for the three force-overridable doctypes, a submitted record for
	the same KYC exists that's been explicitly Force Overridden (see
	force_override() on each of those three doctypes). Shared by
	_nearest_requirement below and Vendor Sign Off's own
	_enforce_mandatory_stages, so the two can't drift apart on what
	"satisfied" means."""
	if frappe.db.exists(doctype, filters):
		return True
	if doctype in FORCE_OVERRIDABLE_DOCTYPES:
		return bool(frappe.db.exists(doctype, {"kyc": filters["kyc"], "docstatus": 1, "force_overridden": 1}))
	return False


def _nearest_requirement(kyc_name, sequence_prefix, settings):
	"""Walks backward through `sequence_prefix` (the stages before the one
	being checked) and returns (doctype, is_satisfied) for the nearest one
	that's actually required — the first stage found that's either Vendor
	KYC itself or currently mandatory, skipping over any stage whose own
	mandatory-setting is off. Returns None if `sequence_prefix` is empty
	(shouldn't normally happen, since Vendor KYC is always first).

	Shared by enforce_sequential_creation (which blocks creation) and
	Vendor KYC's get_available_stages (which decides what to offer as a
	creatable option) so the same rule can't drift between the two."""
	business_type = frappe.db.get_value("Vendor KYC", kyc_name, "business_type")
	for prior_doctype, mandatory_check in reversed(sequence_prefix):
		if prior_doctype == "Vendor KYC":
			return prior_doctype, bool(frappe.db.exists("Vendor KYC", {"name": kyc_name, "docstatus": 1}))

		if mandatory_check and not mandatory_check(business_type, settings):
			continue  # this stage isn't mandatory — check the one before it instead

		filters = {"kyc": kyc_name, "docstatus": 1}
		if prior_doctype == "Vendor Background Check":
			# Overall Status folds the Compliance Checks table into the
			# Result — a stage gate here must honor a compliance failure
			# the same way on_submit's own Supplier-disable logic does, not
			# just the Reference-based Result.
			filters["overall_status"] = "Passed"
		elif prior_doctype == "Vendor Compliance Audit":
			filters["outcome"] = "Passed"
		elif prior_doctype == "Vendor Sampling Evaluation":
			# Same reasoning as Background Check / Compliance Audit above —
			# a submitted-but-Rejected evaluation isn't a satisfied
			# requirement; a Rejected outcome disables the Supplier, so
			# nothing downstream should treat it as "done".
			filters["evaluation_outcome"] = "Approved"

		return prior_doctype, stage_requirement_satisfied(prior_doctype, filters)

	return None


def require_no_active_document_for_kyc(doc):
	"""Call from before_insert() on any stage doctype after Vendor KYC.
	Only one of each stage is ever allowed to be active — draft or
	submitted — per vendor at a time; redoing one means cancelling (and,
	if needed, amending) the existing one first, not creating an
	unrelated second document with no audit trail linking the two
	attempts together. An amendment is exempt automatically: its
	amended-from original is already cancelled by the time this runs, so
	it never matches this filter."""
	if not doc.kyc:
		return
	if frappe.db.exists(doc.doctype, {"kyc": doc.kyc, "docstatus": ["in", [0, 1]]}):
		frappe.throw(
			frappe._(
				"A {0} already exists for this vendor (draft or submitted) — cancel it (and amend it, if"
				" you need to redo it) before creating another."
			).format(doc.doctype)
		)


def _onboarding_request_for(doc):
	"""Resolve the Vendor Onboarding Request that owns `doc` — directly for
	Vendor KYC (which has its own onboarding_request field), otherwise via
	doc.kyc -> Vendor KYC.onboarding_request for the 4 stages after it.
	Returns None if there isn't one to resolve (e.g. a KYC created with no
	onboarding_request at all, or a stage doc with no kyc set yet)."""
	if doc.doctype == "Vendor KYC":
		return doc.onboarding_request
	if not doc.kyc:
		return None
	return frappe.db.get_value("Vendor KYC", doc.kyc, "onboarding_request")


def stopped_state(onboarding_request):
	"""Whether the given Vendor Onboarding Request is currently Stopped, or
	False if there isn't one to check. Single source of truth shared by
	block_if_onboarding_request_stopped below and get_available_stages, so
	the two can't drift on what "stopped" means. The reason for a Stop/
	Re-open lives only as a Comment on the request itself (see stop()/
	reopen()) — never read back here, so the actual enforcement stays one
	cheap lookup on an indexed field, not a comment query."""
	if not onboarding_request:
		return False
	return bool(frappe.db.get_value("Vendor Onboarding Request", onboarding_request, "is_stopped"))


def block_if_onboarding_request_stopped(doc):
	"""Call from validate() (covers create and save — submit runs validate()
	too) and before_cancel() on Vendor KYC and each of the 4 stage doctypes
	after it. This is the real enforcement — get_available_stages() below
	only controls what a UI offers to click; this is what actually stops a
	direct API call, Data Import, or anything else that isn't a button."""
	if not stopped_state(_onboarding_request_for(doc)):
		return
	frappe.throw(
		frappe._(
			"This Vendor Onboarding Request has been stopped — see its Comments for why — re-open it if you want"
			" to proceed."
		)
	)


def enforce_sequential_creation(doc):
	"""Call from validate() on any stage doctype after Vendor KYC. Requires
	the nearest earlier *mandatory* stage to already be submitted before a
	new document for this stage can be created. Always enforced — by
	explicit product decision, not a Settings toggle; the only way to skip
	a stage is that stage's own mandatory checkbox (or, for Sampling, not
	being in the Business Type table — see is_sampling_mandatory)."""
	if not doc.is_new():
		return
	if not doc.kyc:
		return

	names = [name for name, _setting in STAGE_SEQUENCE]
	idx = names.index(doc.doctype)
	settings = frappe.get_single("Vendor Lifecycle Settings")

	requirement = _nearest_requirement(doc.kyc, STAGE_SEQUENCE[:idx], settings)
	if not requirement or requirement[1]:
		return

	prior_doctype = requirement[0]
	if prior_doctype == "Vendor KYC":
		frappe.throw(frappe._("Vendor KYC must be submitted before starting {0}.").format(doc.doctype))
	frappe.throw(frappe._("A submitted {0} is required before starting {1}.").format(prior_doctype, doc.doctype))


def enforce_sequential_cancellation(doc):
	"""Call from on_cancel() on any of the four onboarding stage doctypes
	(Background Check, Compliance Audit, Sampling Evaluation, Sign Off),
	before any other on_cancel side effect. Mirrors enforce_sequential_
	creation()'s own ordering — creation is already blocked out of order,
	but cancellation had no equivalent check at all, so a stage could be
	cancelled while a later stage was still submitted for the same KYC,
	leaving a hole in the middle of the vendor's stage history with
	nothing to detect or prevent it."""
	names = [name for name, _setting in STAGE_SEQUENCE]
	if doc.doctype not in names or not doc.kyc:
		return

	for later_doctype in names[names.index(doc.doctype) + 1 :]:
		existing = frappe.db.get_value(later_doctype, {"kyc": doc.kyc, "docstatus": 1}, "name")
		if existing:
			frappe.throw(
				frappe._(
					"Cancel {0} ({1}) first — it was created after this {2}, for the same Vendor KYC."
				).format(later_doctype, existing, doc.doctype)
			)


@frappe.whitelist()
def get_available_stages(kyc):
	"""Which of the stages after Vendor KYC can currently be created for
	this KYC — a stage already submitted is done and isn't offered again;
	otherwise it's available if its nearest required predecessor (per
	_nearest_requirement above) is satisfied. Order is always enforced (see
	enforce_sequential_creation) — the only way to skip a stage is that
	stage's own mandatory setting. Whitelisted directly (not just through
	Vendor KYC's own wrapper method) so any stage doctype's own form can
	call it too, to offer creating its own immediate next stage — the
	single source of truth for "what's available right now", so KYC's
	"Create" dropdown and each stage's own convenience button can never
	disagree.

	Returns {"stages": [...], "background_check_failed": bool,
	"compliance_audit_failed": bool, "sampling_evaluation_rejected": bool,
	"stopped": bool}. A Failed Background Check, a Failed Compliance Audit,
	or a Rejected Sampling Evaluation is each a hard, unconditional stop on
	everything after it; those flags (and "stopped", checked first — see
	block_if_onboarding_request_stopped above) let a caller show an
	explanatory message for *why* nothing is available, rather than a
	silently empty list that looks the same as "nothing left to do". A
	Force Overridden result (force_overridden=1 — see force_override() on
	each of the three doctypes) lifts the failed/rejected hard stop, same
	as it lifts _nearest_requirement's own check above — Stopped has no
	such override, that's what Re-open is for."""
	empty = {
		"stages": [],
		"background_check_failed": False,
		"compliance_audit_failed": False,
		"sampling_evaluation_rejected": False,
		"sign_off_is_retry": False,
		"stopped": False,
	}

	onboarding_request = frappe.db.get_value("Vendor KYC", kyc, "onboarding_request")
	if stopped_state(onboarding_request):
		return {**empty, "stopped": True}

	if frappe.db.exists(
		"Vendor Background Check", {"kyc": kyc, "docstatus": 1, "overall_status": "Failed", "force_overridden": 0}
	):
		return {**empty, "background_check_failed": True}
	if frappe.db.exists(
		"Vendor Compliance Audit", {"kyc": kyc, "docstatus": 1, "outcome": "Failed", "force_overridden": 0}
	):
		return {**empty, "compliance_audit_failed": True}
	if frappe.db.exists(
		"Vendor Sampling Evaluation",
		{"kyc": kyc, "docstatus": 1, "evaluation_outcome": "Rejected", "force_overridden": 0},
	):
		return {**empty, "sampling_evaluation_rejected": True}

	settings = frappe.get_single("Vendor Lifecycle Settings")
	available = []
	sign_off_is_retry = False

	for i, (doctype, _mandatory_setting) in enumerate(STAGE_SEQUENCE):
		if doctype == "Vendor KYC":
			continue

		# Only one of each stage is ever allowed per vendor at a time (see
		# require_no_active_document_for_kyc) — a draft one already blocks
		# creating another, so it must stop being offered here too, not
		# just once one is submitted. Vendor Sign Off is the one
		# exception (see _require_no_active_signoff_unless_failed on
		# Vendor Sign Off itself): a Submitted-and-Failed one doesn't
		# block a retry there, so it must not read as "already done"
		# here either — otherwise the KYC's own Create button would
		# never offer a retry the doctype itself already allows.
		if doctype == "Vendor Sign Off":
			has_failed_sign_off = frappe.db.exists(doctype, {"kyc": kyc, "docstatus": 1, "sign_off_failed": 1})
			blocked = frappe.db.exists(doctype, {"kyc": kyc, "docstatus": 0}) or frappe.db.exists(
				doctype, {"kyc": kyc, "docstatus": 1, "sign_off_failed": ["!=", 1]}
			)
			if not blocked and has_failed_sign_off:
				sign_off_is_retry = True
		else:
			blocked = frappe.db.exists(doctype, {"kyc": kyc, "docstatus": ["in", [0, 1]]})
		if blocked:
			continue  # already done

		requirement = _nearest_requirement(kyc, STAGE_SEQUENCE[:i], settings)
		if not requirement or requirement[1]:
			available.append(doctype)

	return {**empty, "stages": available, "sign_off_is_retry": sign_off_is_retry}


FORCE_OVERRIDE_ROLES = {"System Manager", "Vendor Lifecycle Manager"}


def can_force_override():
	# The one real security boundary for force_override_stage() below —
	# hardcoded rather than a Settings-configurable role, by explicit
	# product decision (same reasoning as Vendor KYC's freeze/unfreeze
	# roles).
	return bool(FORCE_OVERRIDE_ROLES & set(frappe.get_roles()))


def force_override_stage(doc, reason):
	"""Shared implementation behind force_override() on Vendor Background
	Check / Vendor Compliance Audit / Vendor Sampling Evaluation — the
	only three doctypes with a force_overridden field (see
	FORCE_OVERRIDABLE_DOCTYPES above). Each doctype keeps its own thin
	@frappe.whitelist() force_override(reason) wrapper calling this, so
	frappe.has_permission's normal doctype-level checks still run before
	this ever executes.

	Marks the document Force Overridden with the given reason (so
	stage_requirement_satisfied() above treats it as satisfying the
	pipeline gate it failed), then re-enables the Supplier unless some
	*other* still-active disable reason remains (see
	vendor_creation.get_disable_reason_for_supplier) — deliberately leaves
	is_frozen untouched either way; overriding only lifts the hard
	"disabled" block, not any freeze state."""
	if not can_force_override():
		frappe.throw(
			frappe._("Only a System Manager or Vendor Lifecycle Manager can force-override this result."),
			frappe.PermissionError,
		)
	if doc.docstatus != 1:
		frappe.throw(frappe._("Only a submitted document can be force-overridden."))
	if doc.force_overridden:
		frappe.throw(frappe._("This document has already been force-overridden."))
	if not (reason or "").strip():
		frappe.throw(frappe._("A reason is required to force-override this result."))

	doc.db_set("force_overridden", 1, update_modified=False)
	doc.db_set("force_override_reason", reason.strip(), update_modified=False)

	if doc.vendor:
		from vendor_lifecycle.vendor_lifecycle.vendor_creation import get_disable_reason_for_supplier

		if not get_disable_reason_for_supplier(doc.vendor):
			frappe.db.set_value("Supplier", doc.vendor, "disabled", 0)

	_notify_force_overridden(doc)


def _notify_force_overridden(doc):
	# Reuses the same "Stage Passed" template as a genuine pass — the
	# override_note line is what tells the vendor this one was a manual
	# call, not an actual clean pass.
	try:
		from vendor_lifecycle.vendor_lifecycle.vendor_creation import (
			get_kyc_vendor_contact,
			resolve_vendor_lifecycle_company_name,
			send_vendor_lifecycle_email,
		)

		contact = get_kyc_vendor_contact(doc.kyc) if doc.kyc else {}
		send_vendor_lifecycle_email(
			doctype=doc.doctype,
			name=doc.name,
			template_name="Vendor Lifecycle Stage Passed",
			context={
				"firm_name": contact.get("firm_name"),
				"contact_person_name": contact.get("contact_person_name"),
				"company_name": resolve_vendor_lifecycle_company_name(),
				"stage_name": doc.doctype.replace("Vendor ", ""),
				"next_stage": NEXT_STAGE_LABEL.get(doc.doctype),
				"override_note": frappe._(
					"This was approved by a manager review rather than a clean pass, but you're clear to proceed."
				),
			},
			recipients=[contact["official_email"]] if contact.get("official_email") else [],
		)
	except Exception:
		frappe.log_error(
			title=f"{doc.doctype}: failed to send force-override email", message=frappe.get_traceback()
		)
