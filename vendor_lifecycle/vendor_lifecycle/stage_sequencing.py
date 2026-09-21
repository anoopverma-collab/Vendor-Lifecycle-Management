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
	# The Business Type table is the sole source of truth for onboarding,
	# by explicit product decision — no flat fallback checkbox, and no
	# per-row toggle either: being listed IS being mandatory. A Business
	# Type not in the table at all is simply not mandatory. Public (no
	# leading underscore) since Vendor Sign Off's own onboarding
	# mandatory-stage gate reuses this directly. Re-boarding does NOT call
	# this one directly — see is_sampling_mandatory_for_reboarding below,
	# which gates it behind its own separate "is Sampling mandatory for
	# re-boarding at all" checkbox first.
	if not business_type:
		return False
	return any(row.business_type == business_type for row in settings.get("sampling_mandatory_overrides"))


def is_sampling_mandatory_for_reboarding(business_type, settings):
	# Re-boarding's own gated version — reboarding_sampling_mandatory
	# (a flat Settings checkbox, same shape as reboarding_background_
	# check_mandatory / reboarding_audit_mandatory) is the master switch;
	# only once that's checked does the exact same Business Type table
	# onboarding uses (is_sampling_mandatory, no separate re-boarding-only
	# table) start to matter for a re-boarding run too.
	return bool(settings.get("reboarding_sampling_mandatory")) and is_sampling_mandatory(business_type, settings)


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
	{"kyc": ..., "docstatus": 1, <outcome field>: <passing value>}, or, for
	a re-boarding check, {"reboarding_request": ..., ...} instead) exists
	— OR, for the three force-overridable doctypes, a submitted record
	scoped the same way exists that's been explicitly Force Overridden
	(see force_override() on each of those three doctypes). Shared by
	nearest_requirement below and Vendor Sign Off's own
	_enforce_mandatory_stages, so the two can't drift apart on what
	"satisfied" means."""
	if frappe.db.exists(doctype, filters):
		return True
	if doctype in FORCE_OVERRIDABLE_DOCTYPES:
		# Scoped by whichever key the caller filtered on (kyc for
		# onboarding, reboarding_request for a re-boarding check) — never
		# hardcoded, so a re-boarding-scoped filters dict (which has no
		# "kyc" key at all) doesn't KeyError here.
		scope_field = "reboarding_request" if "reboarding_request" in filters else "kyc"
		return bool(
			frappe.db.exists(
				doctype, {scope_field: filters[scope_field], "docstatus": 1, "force_overridden": 1}
			)
		)
	return False


# (doctype, display label, field holding the pass/fail decision, the value
# that field takes when Passed, the force-override field or None) — shared
# by Vendor Onboarding Request's and Vendor Reboarding Request's own
# get_pipeline_progress(), so the two pipeline-progress widgets can never
# drift apart on what each stage doctype's own outcome actually means.
# Sign Off has no force field: it's not in FORCE_OVERRIDABLE_DOCTYPES above
# — a Failed Sign-off is a terminal decision about the vendor overall, not
# a mid-pipeline gate a manager can force past.
STAGE_OUTCOME_CONFIG = (
	("Vendor Background Check", "Background Check", "overall_status", "Passed", "force_overridden"),
	("Vendor Compliance Audit", "Compliance Audit", "outcome", "Passed", "force_overridden"),
	("Vendor Sampling Evaluation", "Sampling Evaluation", "evaluation_outcome", "Approved", "force_overridden"),
	("Vendor Sign Off", "Sign Off", "sign_off_failed", 0, None),
)


def stage_progress_status(doctype, filters, outcome_field, passed_value, force_field):
	"""A submitted stage document is "Completed" only if it genuinely
	passed — force_overridden is checked first, since force_override_stage()
	never touches the underlying outcome field, so a forcefully-passed
	record still literally reads Failed/Rejected underneath forever;
	that's shown as its own distinct "Forcefully Passed" state rather than
	folded into either Completed or Failed. Sign Off has no force_field
	(it's not force-overridable), so it only ever resolves to Completed or
	Failed.

	docstatus 2 (cancelled) is excluded from the lookup entirely — a
	cancelled record with no fresh replacement isn't "in progress" or any
	prior outcome, it's simply as if nothing exists yet. `filters` is
	{"kyc": kyc_name} for onboarding or {"reboarding_request": name} for
	re-boarding — only one non-cancelled record can exist per that scope
	at a time (see require_no_active_document_for_kyc /
	_require_no_active_signoff_unless_failed), so this is never ambiguous
	about which record to look at."""
	rows = frappe.get_all(
		doctype,
		filters={**filters, "docstatus": ["!=", 2]},
		fields=["docstatus", outcome_field] + ([force_field] if force_field else []),
		limit=1,
	)
	if not rows:
		return "Not Started"

	row = rows[0]
	if row.docstatus == 0:
		return "In Progress"

	# docstatus == 1 from here — Needs Review/Not Reviewed shouldn't be
	# reachable at submit time (every one of these 4 doctypes blocks its
	# own submit while its outcome is still unresolved), so this is a
	# genuine Passed/Failed decision, not a partial one.
	if force_field and row.get(force_field):
		return "Forcefully Passed"
	return "Completed" if row.get(outcome_field) == passed_value else "Failed"


REBOARDING_MANDATORY_SETTINGS_FIELD = {
	"Vendor Background Check": "reboarding_background_check_mandatory",
	"Vendor Compliance Audit": "reboarding_audit_mandatory",
}


def nearest_requirement(kyc_name, sequence_prefix, settings, *, reboarding_request=None):
	"""Walks backward through `sequence_prefix` (the stages before the one
	being checked) and returns (doctype, is_satisfied) for the nearest one
	that's actually required — the first stage found that's either Vendor
	KYC itself (onboarding only — see below) or currently mandatory,
	skipping over any stage whose own mandatory-setting is off. Returns
	None if nothing in `sequence_prefix` is required (shouldn't normally
	happen for onboarding, since Vendor KYC is always first there and
	always required; for re-boarding, an empty/all-optional prefix is
	completely normal — e.g. nothing else is configured mandatory yet).

	Public (no leading underscore), since it's shared not just within
	this module (enforce_sequential_creation, which blocks creation) but
	by Vendor KYC's own get_available_stages (onboarding) and Vendor
	Reboarding Request's own get_reboarding_stage_info (re-boarding), so
	the same rule can't drift between what's blocked and what's offered.

	Pass reboarding_request to get re-boarding's own version instead of
	onboarding's: scoped by reboarding_request rather than kyc for the
	existence checks, and reading re-boarding's own independent mandatory
	settings for Background Check / Compliance Audit (REBOARDING_
	MANDATORY_SETTINGS_FIELD) — ignoring STAGE_SEQUENCE's own onboarding-
	only mandatory_check functions entirely. Sampling is gated by its own
	separate reboarding_sampling_mandatory checkbox first — only once
	that's checked does it fall through to the very same onboarding-wide
	Business Type table (see is_sampling_mandatory_for_reboarding). Vendor
	KYC is never treated as "the nearest requirement" for a
	re-boarding document — re-boarding has no KYC stage of its own to
	gate on; original_kyc is a frozen historical reference, not something
	to require freshly — so it's skipped over entirely rather than ending
	the walk, in case something before it in sequence_prefix could still
	be the real answer (in practice nothing ever is, since KYC is always
	first, but this keeps the loop honest rather than relying on that)."""
	business_type = frappe.db.get_value("Vendor KYC", kyc_name, "business_type")
	for prior_doctype, mandatory_check in reversed(sequence_prefix):
		if prior_doctype == "Vendor KYC":
			if reboarding_request:
				continue
			return prior_doctype, bool(frappe.db.exists("Vendor KYC", {"name": kyc_name, "docstatus": 1}))

		if reboarding_request:
			mandatory = (
				is_sampling_mandatory_for_reboarding(business_type, settings)
				if prior_doctype == "Vendor Sampling Evaluation"
				else bool(settings.get(REBOARDING_MANDATORY_SETTINGS_FIELD[prior_doctype]))
			)
		else:
			mandatory = bool(mandatory_check(business_type, settings)) if mandatory_check else True
		if not mandatory:
			continue  # this stage isn't mandatory — check the one before it instead

		filters = {"docstatus": 1}
		filters["reboarding_request" if reboarding_request else "kyc"] = reboarding_request or kyc_name
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
	it never matches this filter.

	A re-boarding-flagged document (doc.is_reboarding) is scoped to its
	own reboarding_request instead of kyc — the vendor's original,
	already-submitted onboarding-time document for the same kyc must not
	block a fresh re-boarding run of the same doctype. Onboarding's own
	documents (is_reboarding always falsy) are completely unaffected by
	this branch.

	A Renewal-flagged document (doc.is_renewal) has no kyc/reboarding_
	request anchor at all — it stands alone, scoped only by vendor — and
	the rule is deliberately looser: any number of PAST submitted
	Renewals is normal (that's the whole point, a renewal history), only
	a second one still in DRAFT for the same vendor is blocked."""
	if getattr(doc, "is_renewal", False):
		if not doc.vendor:
			return
		if frappe.db.exists(doc.doctype, {"vendor": doc.vendor, "docstatus": 0, "name": ["!=", doc.name or ""]}):
			frappe.throw(
				frappe._(
					"A draft {0} already exists for this vendor — submit or cancel it before creating another."
				).format(doc.doctype)
			)
		return
	if getattr(doc, "is_reboarding", False):
		if not doc.reboarding_request:
			return
		if frappe.db.exists(doc.doctype, {"reboarding_request": doc.reboarding_request, "docstatus": ["in", [0, 1]]}):
			frappe.throw(
				frappe._(
					"A {0} already exists for this re-boarding request (draft or submitted) — cancel it (and"
					" amend it, if you need to redo it) before creating another."
				).format(doc.doctype)
			)
		return
	if not doc.kyc:
		return
	if frappe.db.exists(doc.doctype, {"kyc": doc.kyc, "docstatus": ["in", [0, 1]]}):
		frappe.throw(
			frappe._(
				"A {0} already exists for this vendor (draft or submitted) — cancel it (and amend it, if"
				" you need to redo it) before creating another."
			).format(doc.doctype)
		)


def require_active_supplier_for_renewal(doc):
	"""Call from before_insert() AND before_submit() on Vendor Compliance
	Audit, Vendor Sign Off, and Vendor Sampling Evaluation, only when
	doc.is_renewal is set (their shared flag for a standalone Renewal/
	Ad-hoc, independent of onboarding/re-boarding).

	Those three deliberately skip the whole onboarding/re-boarding
	sequencing (see require_no_active_document_for_kyc's own is_renewal
	branch above) - but that same bypass means nothing else stops one
	being created or submitted against a vendor that's since been
	deboarded or frozen, which doesn't make sense for a periodic recheck
	of an already-active vendor. Checked again at submit, not just
	creation, since the vendor's state can change while a draft sits
	open. Deliberately never called for a plain onboarding/re-boarding
	document - those follow their own pipeline rules and must stay
	completely unaffected."""
	if not doc.is_renewal or not doc.vendor:
		return
	disabled, is_frozen = frappe.db.get_value("Supplier", doc.vendor, ["disabled", "is_frozen"])
	if disabled:
		frappe.throw(
			frappe._("{0} is currently disabled — Renewal/Ad-hoc checks only apply to an active vendor.").format(
				doc.vendor
			)
		)
	if is_frozen:
		frappe.throw(
			frappe._("{0} is currently frozen — Renewal/Ad-hoc checks only apply to an active vendor.").format(
				doc.vendor
			)
		)


def block_if_reboarding_completed(doc):
	"""Call from before_insert() on Background Check / Compliance Audit /
	Sampling Evaluation (not Sign Off — a passed Sign Off already blocks a
	second one of itself via _require_no_active_signoff_unless_failed's
	own "blocked" check), for a re-boarding-flagged document only. Once a
	re-boarding run's own Sign Off has actually passed, re-boarding is
	done — nothing else should still be creatable against that same
	Vendor Reboarding Request afterward."""
	if not getattr(doc, "is_reboarding", False) or not doc.reboarding_request:
		return
	if frappe.db.exists(
		"Vendor Sign Off", {"reboarding_request": doc.reboarding_request, "docstatus": 1, "sign_off_failed": 0}
	):
		frappe.throw(
			frappe._(
				"{0} already has a passed Sign Off — re-boarding is complete, and nothing else can be"
				" created against it."
			).format(frappe.bold(doc.reboarding_request))
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
	the nearest earlier *mandatory* stage to already be submitted (passed,
	or force-overridden) before a new document for this stage can be
	created. Always enforced — by explicit product decision, not a
	Settings toggle; the only way to skip a stage is that stage's own
	mandatory checkbox (or, for Sampling, not being in the Business Type
	table — see is_sampling_mandatory).

	Has its own re-boarding-flagged branch too — re-boarding still does
	NOT enforce a fixed order among Background Check / Compliance Audit /
	Sampling Evaluation in general (each can still be started before the
	others, if neither is configured mandatory), but whichever ones ARE
	mandatory for re-boarding still gate whatever comes after them in the
	normal stage sequence — same "nearest mandatory requirement" rule
	onboarding always had (see nearest_requirement), just reading
	re-boarding's own independent settings and scoped by
	reboarding_request instead of kyc."""
	if not doc.is_new():
		return
	if getattr(doc, "is_renewal", False):
		# A Renewal stands entirely outside the onboarding/re-boarding
		# stage sequence — it doesn't gate, and isn't gated by, Background
		# Check/Compliance Audit/Sampling Evaluation order.
		return

	names = [name for name, _setting in STAGE_SEQUENCE]
	if doc.doctype not in names:
		return
	idx = names.index(doc.doctype)
	settings = frappe.get_single("Vendor Lifecycle Settings")

	if getattr(doc, "is_reboarding", False):
		if not doc.reboarding_request:
			return
		kyc_name = doc.kyc or frappe.db.get_value(
			"Vendor Reboarding Request", doc.reboarding_request, "original_kyc"
		)
		requirement = nearest_requirement(
			kyc_name, STAGE_SEQUENCE[:idx], settings, reboarding_request=doc.reboarding_request
		)
		if not requirement or requirement[1]:
			return
		frappe.throw(
			frappe._(
				"A passed (or force-overridden) {0} is required before starting {1} for this re-boarding request."
			).format(requirement[0], doc.doctype)
		)
		return

	if not doc.kyc:
		return

	requirement = nearest_requirement(doc.kyc, STAGE_SEQUENCE[:idx], settings)
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
	nothing to detect or prevent it.

	Has its own re-boarding-flagged branch too, scoped by reboarding_
	request instead of kyc — re-boarding doesn't enforce stage ORDER at
	creation time (see enforce_sequential_creation's own is_reboarding
	branch), but the same protection this function gives onboarding still
	matters once documents exist: don't let someone cancel a re-boarding
	Background Check while a re-boarding Sign Off that already relied on
	it is still submitted. Scoping by reboarding_request (never kyc) is
	what keeps this from colliding with the vendor's ORIGINAL onboarding-
	time documents — same kyc, but a completely different, already-settled
	pipeline run, which must never be treated as "a later stage" blocking
	a re-boarding document's own cancellation."""
	names = [name for name, _setting in STAGE_SEQUENCE]
	if doc.doctype not in names:
		return
	if getattr(doc, "is_renewal", False):
		# Same reasoning as enforce_sequential_creation's own is_renewal
		# branch — a Renewal has no place in this sequence to protect.
		return

	if getattr(doc, "is_reboarding", False):
		scope_field, scope_value = "reboarding_request", doc.reboarding_request
	else:
		scope_field, scope_value = "kyc", doc.kyc
	if not scope_value:
		return

	for later_doctype in names[names.index(doc.doctype) + 1 :]:
		existing = frappe.db.get_value(later_doctype, {scope_field: scope_value, "docstatus": 1}, "name")
		if existing:
			frappe.throw(
				frappe._(
					"Cancel {0} ({1}) first — it was created after this {2}, for the same {3}."
				).format(
					later_doctype,
					existing,
					doc.doctype,
					frappe._("Vendor Reboarding Request") if scope_field == "reboarding_request" else frappe._("Vendor KYC"),
				)
			)


@frappe.whitelist()
def get_available_stages(kyc):
	"""Which of the stages after Vendor KYC can currently be created for
	this KYC — a stage already submitted is done and isn't offered again;
	otherwise it's available if its nearest required predecessor (per
	nearest_requirement above) is satisfied. Order is always enforced (see
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
	as it lifts nearest_requirement's own check above — Stopped has no
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

		requirement = nearest_requirement(kyc, STAGE_SEQUENCE[:i], settings)
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
	if getattr(doc, "is_renewal", False):
		# A Renewal isn't gating anything downstream (see
		# enforce_sequential_creation's own is_renewal branch) — there's
		# nothing to "push past". It also has no kyc/reboarding_request to
		# scope the disable-revert/status-revert logic below by, and a
		# Renewal is never allowed to touch vendor_lifecycle_status/stage
		# at all (see each Renewal-capable doctype's own on_submit). The
		# correct remedy for a Failed Renewal is a fresh Renewal, not an
		# override.
		frappe.throw(
			frappe._(
				"This result cannot be force-overridden — it isn't part of the onboarding/re-boarding"
				" sequence. Create a new one instead."
			)
		)
	if doc.docstatus != 1:
		frappe.throw(frappe._("Only a submitted document can be force-overridden."))
	if doc.force_overridden:
		frappe.throw(frappe._("This document has already been force-overridden."))
	if not (reason or "").strip():
		frappe.throw(frappe._("A reason is required to force-override this result."))

	doc.db_set("force_overridden", 1, update_modified=False)
	doc.db_set("force_override_reason", reason.strip(), update_modified=False)

	from vendor_lifecycle.vendor_lifecycle.vendor_creation import get_disable_reason_for_supplier, revert_stage_result

	# Deliberately skipped for a re-boarding-flagged document — a
	# re-boarding vendor must stay disabled/frozen until its own final
	# Sign Off actually passes, exactly like a fresh onboarding vendor
	# stays disabled until its first Sign Off; overriding a failed
	# mid-pipeline re-boarding check must not re-enable the Supplier
	# early.
	is_reboarding = getattr(doc, "is_reboarding", False)
	if doc.vendor and not is_reboarding:
		if not get_disable_reason_for_supplier(doc.vendor):
			frappe.db.set_value("Supplier", doc.vendor, "disabled", 0)

	# The fine vendor_lifecycle_status / coarse vendor_lifecycle_stage
	# "Failed" markers still need reverting either way, though — force-
	# overriding is exactly the same kind of resolution as cancelling the
	# failed record, for status-display purposes, in both pipelines.
	if doc.vendor:
		if is_reboarding:
			revert_stage_result(doc.vendor, reboarding_request=doc.reboarding_request)
		else:
			revert_stage_result(doc.vendor, kyc=doc.kyc)

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
				# Re-boarding doesn't enforce a stage order (see
				# enforce_sequential_creation's is_reboarding branch), so
				# "your next stage is X" doesn't hold for it — suppressed
				# rather than shown with a misleading onboarding-only hint.
				"next_stage": None if getattr(doc, "is_reboarding", False) else NEXT_STAGE_LABEL.get(doc.doctype),
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
