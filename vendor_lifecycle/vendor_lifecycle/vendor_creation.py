# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED = "KYC Verified"

# Every doctype that can be configured in Settings as the point where the
# Supplier gets created, other than Vendor KYC itself.
SIBLING_DOCTYPES = [
	"Vendor Background Check",
	"Vendor Compliance Audit",
	"Vendor Sampling Evaluation",
	"Vendor Sign Off",
]


def is_kyc_rejected(kyc_name):
	"""True if this Vendor KYC is Rejected — via our own manual Reject
	button's status field, or via an active Frappe Workflow's own state
	field if one is configured on Vendor KYC. Our Reject button hides
	itself whenever a workflow exists (see the "Vendor KYC Reject Button"
	client script), so a workflow-driven rejection never touches our
	`status` field at all — this has to be checked separately, through
	whatever field and labels that workflow itself uses. Assumes a
	workflow's rejection state is literally named "Rejected", matching
	this app's own convention — a workflow using a different label for it
	won't be recognized here."""
	if frappe.db.get_value("Vendor KYC", kyc_name, "status") == "Rejected":
		return True

	workflow_state_field = frappe.db.get_value(
		"Workflow", {"document_type": "Vendor KYC", "is_active": 1}, "workflow_state_field"
	)
	if not workflow_state_field:
		return False

	return frappe.db.get_value("Vendor KYC", kyc_name, workflow_state_field) == "Rejected"


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


def mark_vendor_status_in_progress(doc, status):
	"""Flags the Supplier's status as "<Stage> In Progress" the moment this
	stage's document is first created — before it's actually submitted or
	passed. Call from validate() on any of SIBLING_DOCTYPES, after
	sync_vendor_field(). Only fires once, on creation (is_new()) — later
	saves of the same still-draft document don't need to re-set it, and
	on_submit() overwrites it with the real "Verified"/"Approved" status
	anyway once the stage actually completes."""
	if doc.is_new() and doc.vendor:
		frappe.db.set_value("Supplier", doc.vendor, "vendor_lifecycle_status", status)


def sync_onboarding_request_field(doc):
	"""Backfill `onboarding_request` from the linked KYC, once available.
	Call from validate() on any of SIBLING_DOCTYPES, alongside
	sync_vendor_field — a no-op once already set, and a no-op if the KYC
	itself has no Onboarding Request (direct-creation KYCs, when Settings
	allows those)."""
	if doc.onboarding_request or not doc.kyc:
		return
	onboarding_request = frappe.db.get_value("Vendor KYC", doc.kyc, "onboarding_request")
	if onboarding_request:
		doc.onboarding_request = onboarding_request


def maybe_create_vendor(kyc):
	"""Call from Vendor KYC's on_submit. Creates the Supplier if none exists
	yet for this KYC, then backfills `vendor` onto any sibling stage
	documents already created for it — possible when a stage document is
	created before its KYC is submitted (e.g. with "Enforce Sequential
	Stages" off). Returns the Supplier doc, or None if one already existed."""
	if kyc.supplier:
		return None

	supplier = _create_or_update_supplier(kyc)
	_backfill_sibling_vendor_fields(kyc.name, supplier.name)

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
	"legal_entity_type": "legal_entity_type",
	"business_type": "business_type",
	"is_transporter": "is_transporter",
	"is_internal_supplier": "is_internal_supplier",
	"represents_company": "represents_company",
	"gstin_uin": "gstin",
	"pan_card": "pan",
	"tax_id": "tax_id",
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

	# Child table rows belong to their own parent — copying the row objects
	# across as-is (like the plain field map above does) would leave them
	# pointing at the wrong parent, so each row's own values are copied into
	# a fresh row on the Supplier instead.
	for row in kyc.get("accounts") or []:
		supplier.append("accounts", {
			"company": row.company,
			"account": row.account,
			"advance_account": row.advance_account,
		})

	supplier.vendor_lifecycle_status = VENDOR_LIFECYCLE_STATUS_KYC_VERIFIED
	# is_frozen is core ERPNext's own broader block — checked centrally on
	# every transaction doctype with a party (Purchase Order, Invoice,
	# Payment Entry, GL Entry, etc.), unlike the narrower on_hold/hold_type
	# ("Block Supplier") mechanism this used to use. It's also made
	# read-only on the Supplier form for any Supplier with vendor_kyc set
	# (see the Property Setter) — the only way to change it from here on is
	# the "Freeze/Unfreeze Supplier" button on Vendor KYC, or automatically
	# on Sign Off.
	supplier.is_frozen = 1
	supplier.vendor_kyc = kyc.name
	supplier.onboarding_request = kyc.onboarding_request

	is_new_supplier = supplier.is_new()
	try:
		if is_new_supplier:
			supplier.insert(ignore_permissions=True)
		else:
			supplier.save(ignore_permissions=True)
	except Exception as e:
		# If the caught error itself came from frappe.throw(), it already
		# queued its own message for automatic display — left in place,
		# the user would see both that raw error AND the friendlier one
		# below, stacked together. A no-op if nothing was queued (e.g. a
		# plain Python exception, not a frappe.throw()).
		frappe.clear_last_message()
		frappe.throw(
			frappe._("Could not create the Supplier automatically: {0}").format(str(e)),
			title=frappe._("Supplier Creation Failed"),
		)

	kyc.db_set("supplier", supplier.name)

	if is_new_supplier:
		try:
			address = _create_addresses_for_supplier(kyc, supplier)
		except Exception as e:
			# See the matching comment on the Supplier-creation try/except
			# above — without this, the raw underlying error would show
			# alongside this friendlier wrapped one.
			frappe.clear_last_message()
			frappe.throw(
				frappe._("Supplier {0} was created, but the Address could not be created automatically: {1}").format(
					frappe.bold(supplier.name), str(e)
				),
				title=frappe._("Address Creation Failed"),
			)

		try:
			contact = make_contact_for_supplier(supplier, *_contact_args_from_kyc(kyc))
		except Exception as e:
			# See the matching comment on the Supplier-creation try/except
			# above — without this, the raw underlying error would show
			# alongside this friendlier wrapped one.
			frappe.clear_last_message()
			frappe.throw(
				frappe._("Supplier {0} was created, but the Contact could not be created automatically: {1}").format(
					frappe.bold(supplier.name), str(e)
				),
				title=frappe._("Contact Creation Failed"),
			)

		try:
			bank_account = make_bank_account_for_supplier(supplier, *_bank_account_args_from_kyc(kyc))
		except Exception as e:
			# See the matching comment on the Supplier-creation try/except
			# above — without this, the raw underlying error would show
			# alongside this friendlier wrapped one.
			frappe.clear_last_message()
			frappe.throw(
				frappe._(
					"Supplier {0} was created, but the Bank Account could not be created automatically: {1}"
				).format(frappe.bold(supplier.name), str(e)),
				title=frappe._("Bank Account Creation Failed"),
			)
		# These three read-only fields on Vendor KYC point at whatever this
		# automatic, on-submit pass produced — the reviewer's own reference
		# for "what did submitting this KYC actually create". Records made
		# later via the KYC's "Create Address/Contact/Bank Account" buttons
		# are additional/extra ones and intentionally don't overwrite these.
		if address:
			kyc.db_set("address", address.name)
		if contact:
			kyc.db_set("contact", contact.name)
		if bank_account:
			kyc.db_set("bank_account", bank_account.name)

	return supplier


def _contact_args_from_kyc(kyc):
	first_name, _, last_name = (kyc.contact_person_name or "").partition(" ")
	return first_name or None, last_name or None, kyc.official_email, kyc.contact_person_number


def _bank_account_args_from_kyc(kyc):
	return (
		kyc.bank_account_name, kyc.bank, kyc.bank_account_no, kyc.ifsc_branch_code, kyc.iban, kyc.account_type,
		kyc.is_default_bank_account,
	)


def _create_addresses_for_supplier(kyc, supplier):
	"""Creates one Address record if the reviewer confirmed the GSTIN address
	matches the one filled in manually, or both as separate records if they
	said the two differ. A no-op for whichever side has nothing to create
	from — e.g. a vendor outside India never has a GSTIN address at all.
	Returns the first Address created (for the read-only reference field on
	Vendor KYC), or None if neither side had anything to create."""
	if kyc.is_address_same_as_gstin:
		if kyc.gstin_address_line and kyc.gstin_city:
			return make_address_for_supplier(
				supplier, kyc.address_type or "Billing", kyc.gstin_address_line, None, kyc.gstin_city,
				kyc.gstin_state, kyc.gstin_pincode, "India", kyc.gstin_uin,
			)
		return None

	first_address = None
	if kyc.address_line_1 and kyc.city:
		first_address = make_address_for_supplier(
			supplier, kyc.address_type or "Billing", kyc.address_line_1, kyc.address_line_2, kyc.city, kyc.state,
			kyc.pincode, kyc.country or "India", kyc.gstin_uin,
		)

	if kyc.gstin_address_line and kyc.gstin_city:
		second_address = make_address_for_supplier(
			supplier, "Billing", kyc.gstin_address_line, None, kyc.gstin_city, kyc.gstin_state, kyc.gstin_pincode,
			"India", kyc.gstin_uin,
		)
		first_address = first_address or second_address

	return first_address


def make_address_for_supplier(
	supplier, address_type, address_line1, address_line2, city, state, pincode, country, gstin=None
):
	"""Creates one Address linked to `supplier`. Used both for the automatic,
	on-submit creation above, and directly from Vendor KYC's "Create Address"
	button — the latter passes in values a reviewer typed into a dialog,
	rather than anything read off the KYC document itself."""
	address = frappe.new_doc("Address")
	address.address_title = supplier.supplier_name
	address.address_type = address_type
	address.address_line1 = address_line1
	address.address_line2 = address_line2
	address.city = city
	address.state = state
	address.pincode = pincode
	address.country = country
	address.append("links", {"link_doctype": "Supplier", "link_name": supplier.name})
	address.vendor_kyc = supplier.vendor_kyc
	# india_compliance's own Custom Field on Address — only exists at all
	# when that app is installed. Set whenever the KYC has a GSTIN, on every
	# address created for this vendor (not just the one that happens to be
	# the GSTIN-registered one), since it's a property of the firm, not of
	# any one specific address.
	if gstin and "india_compliance" in frappe.get_installed_apps():
		address.gstin = gstin
	address.insert(ignore_permissions=True)
	return address


def make_contact_for_supplier(supplier, first_name, last_name, email, phone):
	"""Creates one Contact linked to `supplier`. A no-op if there's no first
	name to build a Contact from — that's the only thing this treats as
	required, since neither Contact's own doctype nor Frappe's controller
	enforces one. Used both for the automatic, on-submit creation above, and
	directly from Vendor KYC's "Create Contact" button."""
	if not first_name:
		return None

	contact = frappe.new_doc("Contact")
	contact.first_name = first_name
	contact.last_name = last_name
	contact.company_name = supplier.supplier_name
	contact.is_primary_contact = 1

	if email:
		contact.email_id = email
		contact.append("email_ids", {"email_id": email, "is_primary": 1})

	if phone:
		contact.phone = phone
		contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1})

	contact.append("links", {"link_doctype": "Supplier", "link_name": supplier.name})
	contact.vendor_kyc = supplier.vendor_kyc
	contact.insert(ignore_permissions=True)
	return contact


def make_bank_account_for_supplier(supplier, account_name, bank, bank_account_no, branch_code, iban, account_type, is_default):
	"""Creates one Bank Account linked to `supplier`. A no-op unless both
	Account Name and Bank are given — Bank Account's own doctype requires
	both, so there's nothing valid to create without them. Used both for the
	automatic, on-submit creation above, and directly from Vendor KYC's
	"Create Bank Account" button."""
	if not (account_name and bank):
		return None

	bank_account = frappe.new_doc("Bank Account")
	bank_account.account_name = account_name
	bank_account.bank = bank
	bank_account.bank_account_no = bank_account_no
	bank_account.branch_code = branch_code
	# Bank Account has no field for SWIFT/BIC — IBAN is the only one of the
	# two international details it can actually hold.
	bank_account.iban = iban
	bank_account.account_type = account_type
	bank_account.is_default = is_default
	bank_account.party_type = "Supplier"
	bank_account.party = supplier.name
	bank_account.vendor_kyc = supplier.vendor_kyc
	bank_account.insert(ignore_permissions=True)
	return bank_account


def _backfill_sibling_vendor_fields(kyc_name, supplier_name):
	for doctype in SIBLING_DOCTYPES:
		filters = {"kyc": kyc_name, "vendor": ["in", ("", None)]}
		for name in frappe.get_all(doctype, filters=filters, pluck="name"):
			frappe.db.set_value(doctype, name, "vendor", supplier_name)


def validate_supplier_vendor_kyc(doc, method=None):
	"""Wired via hooks.py doc_events on Supplier. Both checks read straight
	from the database (not a cached value) — deliberately, since this runs
	mid-transaction during Vendor KYC's own on_submit (docstatus flips to
	submitted before on_submit fires, so a live DB read already sees it as
	submitted; a cached read might not)."""
	if (
		doc.is_new()
		and not doc.vendor_kyc
		and not doc.flags.get("ignore_vendor_lifecycle_restriction")
		and frappe.db.get_single_value("Vendor Lifecycle Settings", "restrict_supplier_creation_to_vendor_lifecycle")
	):
		# is_new() is what keeps this from retroactively blocking existing
		# Suppliers that already have no Vendor KYC (created before this
		# setting existed, or unrelated to this app entirely) — only a
		# brand-new Supplier is required to have one. The flags check is a
		# deliberate escape hatch for internal/test code that legitimately
		# needs a plain Supplier with nothing to do with this app (e.g.
		# portal-permission or satisfaction-survey tests) — never set from
		# the Desk UI or any real creation path.
		frappe.throw(
			frappe._("A Supplier can only be created from the Vendor Lifecycle module. Link a Vendor KYC."),
			frappe.MandatoryError,
		)

	if not doc.vendor_kyc:
		return

	docstatus = frappe.db.get_value("Vendor KYC", doc.vendor_kyc, "docstatus")
	if docstatus is None:
		frappe.throw(frappe._("Vendor KYC {0} does not exist.").format(frappe.bold(doc.vendor_kyc)))
	if docstatus != 1:
		frappe.throw(
			frappe._("Vendor KYC {0} must be submitted before it can be linked to a Supplier.").format(
				frappe.bold(doc.vendor_kyc)
			)
		)

	duplicate = frappe.db.get_value(
		"Supplier", {"vendor_kyc": doc.vendor_kyc, "name": ["!=", doc.name or ""]}, "name"
	)
	if duplicate:
		frappe.throw(
			frappe._("Vendor KYC {0} is already linked to Supplier {1}.").format(
				frappe.bold(doc.vendor_kyc), frappe.bold(duplicate)
			)
		)


# Checked in reverse pipeline order (latest stage first) — a disabled
# Supplier normally has exactly one of these, but "Enforce Sequential
# Stages" being off makes more than one theoretically possible; showing
# the latest stage's failure is the most likely to actually be the
# current, actionable reason. Vendor Sign Off is NOT in this list — see
# get_disable_reason_for_supplier()'s own handling of it below, since
# (unlike these three) more than one submitted Sign Off can legitimately
# exist per vendor at once (a Failed one kept for history, plus a retry).
DISABLE_REASON_SOURCES = [
	# force_overridden: 0 — an overridden result is no longer a live reason
	# the Supplier is (or should stay) disabled; see force_override() on
	# each of these three doctypes.
	("Vendor Sampling Evaluation", {"evaluation_outcome": "Rejected", "force_overridden": 0}),
	("Vendor Compliance Audit", {"outcome": "Failed", "force_overridden": 0}),
	("Vendor Background Check", {"overall_status": "Failed", "force_overridden": 0}),
]


def get_disable_reason_for_supplier(supplier):
	"""Supplier.disabled is set automatically by a Failed Background
	Check, a Failed Compliance Audit, a Rejected Sampling Evaluation, or a
	Failed Sign Off — and only ever reverted by that same record being
	cancelled (see each doctype's own _revert_disable_if_this_was_the_
	failed_one / _revert_vendor_status_to_last_completed_stage). Returns
	whichever submitted record is responsible, if any."""
	# Sign Off is checked first (latest stage = most likely current
	# reason, same as the DISABLE_REASON_SOURCES ordering below), but by
	# its MOST RECENT submitted record only — a vendor can have an old
	# Failed Sign Off retried by a newer one, and only the newer one's
	# own outcome should ever count as a live reason. A plain "does any
	# Failed Sign Off exist" filter would keep blaming a superseded,
	# already-retried failure forever.
	latest_sign_off = frappe.db.get_value(
		"Vendor Sign Off",
		{"vendor": supplier, "docstatus": 1},
		["name", "sign_off_failed"],
		as_dict=True,
		order_by="creation desc",
	)
	if latest_sign_off and latest_sign_off.sign_off_failed:
		return {"doctype": "Vendor Sign Off", "name": latest_sign_off.name}

	for doctype, filters in DISABLE_REASON_SOURCES:
		name = frappe.db.get_value(doctype, {"vendor": supplier, "docstatus": 1, **filters}, "name")
		if name:
			return {"doctype": doctype, "name": name}
	return None


def set_supplier_disable_reason_onload(doc, method=None):
	"""Wired via hooks.py doc_events on Supplier's "onload" — attaches
	whichever record disabled this Supplier directly onto the document as
	it's loaded for display, so the form's own JS can show an explanatory
	ribbon synchronously from frm.doc.__onload, with no separate
	client-side round-trip (and so no risk of that round-trip's response
	arriving after some other script's own refresh() has already run and
	touched the same intro/headline banner slot)."""
	if not (doc.vendor_kyc and doc.disabled):
		return
	reason = get_disable_reason_for_supplier(doc.name)
	if reason:
		doc.set_onload("disable_reason", reason)


# Shared by every Vendor Lifecycle email sender (Vendor Onboarding Request,
# Vendor KYC, each stage doctype, and Vendor Sign Off) — kept in this
# neutral module rather than on any one doctype's own controller, since
# vendor_sign_off.py already imports from the three stage doctypes and
# putting these on Sign Off would make importing them back into those
# three a circular import.
VENDOR_LIFECYCLE_MANAGER_ROLE = "Vendor Lifecycle Manager"


def vendor_lifecycle_manager_emails():
	# Every enabled user holding the Vendor Lifecycle Manager role — always
	# CC'd on every Vendor Lifecycle email, regardless of what's in
	# Settings' own Always CC list.
	user_names = frappe.get_all(
		"Has Role", filters={"role": VENDOR_LIFECYCLE_MANAGER_ROLE, "parenttype": "User"}, pluck="parent"
	)
	if not user_names:
		return []
	return frappe.get_all("User", filters={"name": ["in", user_names], "enabled": 1}, pluck="email")


def vendor_lifecycle_cc_list(settings):
	cc = [addr.strip() for addr in (settings.vendor_lifecycle_email_always_cc or "").split(",") if addr.strip()]
	for email in vendor_lifecycle_manager_emails():
		if email and email not in cc:
			cc.append(email)
	return cc


def vendor_lifecycle_emails_enabled(settings=None):
	# The master switch — every email sender in the app checks this first.
	# When off, nothing here should ever try to send anything at all.
	settings = settings or frappe.get_single("Vendor Lifecycle Settings")
	return bool(settings.enable_vendor_lifecycle_emails)


def require_vendor_lifecycle_email_account(settings):
	# No fallback, by explicit design: if this isn't set (or the account it
	# points to is missing/disabled), sending is blocked outright rather
	# than silently going out from whatever default account the site
	# happens to have configured for other purposes.
	if not settings.vendor_lifecycle_email_account:
		frappe.throw(frappe._("Set an Email Account in Vendor Lifecycle Settings first."))
	account = frappe.db.get_value(
		"Email Account",
		settings.vendor_lifecycle_email_account,
		["name", "email_id", "enable_outgoing"],
		as_dict=True,
	)
	if not account:
		frappe.throw(
			frappe._("The Vendor Lifecycle Email Account {0} no longer exists — update Vendor Lifecycle Settings.").format(
				frappe.bold(settings.vendor_lifecycle_email_account)
			)
		)
	if not account.enable_outgoing:
		frappe.throw(
			frappe._("The Vendor Lifecycle Email Account {0} is disabled for outgoing mail.").format(
				frappe.bold(settings.vendor_lifecycle_email_account)
			)
		)
	return account


def get_kyc_vendor_contact(kyc_name):
	# Shared by every stage doctype after Vendor KYC (Background Check /
	# Compliance Audit / Sampling Evaluation) — none of them carry their
	# own copy of the vendor's email/contact/firm name, only a `kyc` Link.
	return (
		frappe.db.get_value(
			"Vendor KYC", kyc_name, ["official_email", "contact_person_name", "firm_name"], as_dict=True
		)
		or {}
	)


def resolve_vendor_lifecycle_company_name():
	# "The receiving org" for every new stage-outcome email (Onboarding
	# Request, Vendor KYC, and each of the three stage doctypes) — none of
	# those carry Vendor Sign Off's own per-document Company override, so
	# this is just Global Defaults, with a plain fallback so the template
	# never renders a blank "{{ company_name }}".
	return frappe.defaults.get_global_default("company") or frappe._("our team")


def send_vendor_lifecycle_email(doctype, name, template_name, context, recipients, extra_cc=None):
	"""Shared low-level sender for every new, simple (no-attachment)
	Vendor Lifecycle notification — Onboarding Request received/approved,
	KYC completed/rejected, and each stage doctype's own passed/failed/
	force-overridden outcome. Best-effort by design (never raises — the
	caller is expected to wrap this in its own try/except so a broken
	template or account never blocks the real state change it's
	reporting on), same as Vendor Sign Off's own outcome-email sender.

	Silently does nothing if "Use Emails" is off, if `recipients` is
	empty, or if a template of `template_name` doesn't exist — deliberate,
	since this always runs from a doc_events-style hook where a hard
	failure would be surprising."""
	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not vendor_lifecycle_emails_enabled(settings):
		return
	if not recipients:
		return
	if not frappe.db.exists("Email Template", template_name):
		return

	template = frappe.get_doc("Email Template", template_name)
	subject = template.get_formatted_subject(context)
	message = template.get_formatted_response(context)

	cc = vendor_lifecycle_cc_list(settings)
	for addr in extra_cc or []:
		if addr and addr not in cc:
			cc.append(addr)
	# Don't CC an address that's already a direct recipient (e.g. an
	# internal-only notification where every recipient already is a
	# Vendor Lifecycle Manager) — looks odd sitting in both headers.
	cc = [addr for addr in cc if addr not in recipients]

	# Best-effort account resolution, unlike require_vendor_lifecycle_
	# email_account()'s no-fallback requirement for an explicit user
	# action — these notifications must still go out even if the account
	# is missing or misconfigured.
	account = None
	if settings.vendor_lifecycle_email_account:
		account = frappe.db.get_value(
			"Email Account", settings.vendor_lifecycle_email_account, ["name", "email_id"], as_dict=True
		)

	from frappe.core.doctype.communication.email import make as make_communication

	result = make_communication(
		doctype=doctype,
		name=name,
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


DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE = "Vendor Lifecycle Manual Attachment Needed"


def notify_manual_attach_needed(doctype, name, owner, reason):
	"""Shared by signoff_reply.py and checklist_clearance_reply.py — an
	inbound reply couldn't be auto-attached (the vendor forwarded instead
	of replying, breaking Frappe's own In-Reply-To threading, or replied
	with more than one attachment) — the Creator and Vendor Lifecycle
	Manager are told to check the document and attach it by hand."""
	try:
		_notify_manual_attach_needed_unsafe(doctype, name, owner, reason)
	except Exception:
		frappe.log_error(
			title=f"{doctype}: failed to send manual-attach-needed email", message=frappe.get_traceback()
		)


def _notify_manual_attach_needed_unsafe(doctype, name, owner, reason):
	creator_email = frappe.db.get_value("User", owner, "email") or owner
	if not creator_email:
		return
	settings = frappe.get_single("Vendor Lifecycle Settings")
	if not vendor_lifecycle_emails_enabled(settings):
		return
	if not frappe.db.exists("Email Template", DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE):
		return
	cc = vendor_lifecycle_cc_list(settings)

	template = frappe.get_doc("Email Template", DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE)
	context = {
		"doctype_label": doctype,
		"document_name": name,
		"document_link": frappe.utils.get_url_to_form(doctype, name),
		"reason": reason,
	}
	subject = template.get_formatted_subject(context)
	message = template.get_formatted_response(context)

	from frappe.core.doctype.communication.email import make as make_communication

	make_communication(
		doctype=doctype,
		name=name,
		subject=subject,
		content=message,
		recipients=creator_email,
		cc=", ".join(cc) if cc else None,
		send_email=True,
	)
