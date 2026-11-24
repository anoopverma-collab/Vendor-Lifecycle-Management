# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import glob
import os

import frappe
from frappe.modules.import_file import import_file_by_path

# Frappe does not auto-import "Workspace Sidebar" / "Desktop Icon" standard
# files the way it does for DocType/Report/Page/Workspace — both are built
# dynamically from Workspace.shortcuts on install instead, which ignores
# these files entirely. Import them explicitly so the sidebar/app-icon this
# app ships are always in place, on install and on every migrate.
STANDARD_FILES = [
	("workspace_sidebar", "vendor_lifecycle.json"),
	("desktop_icon", "vendor_lifecycle.json"),
]


def sync_standard_files():
	for folder, filename in STANDARD_FILES:
		path = frappe.get_app_path("vendor_lifecycle", folder, filename)
		if os.path.exists(path):
			import_file_by_path(path, force=True)


def sync_web_forms():
	"""One-time only — see patches/seed_web_forms_once.py. Web Forms are
	"is_standard" (JSON-file-owned, like a DocType or Page), but migrate's
	automatic module sync doesn't cover them the way it does DocType/Page/
	Report/Workspace — an edit to a Web Form's JSON otherwise just sits on
	disk forever without ever reaching the database, so some explicit
	import really is needed. This used to run every single migrate though
	(same mechanism, and same bug, as the Desktop Icon/Workspace Sidebar
	fix) — any later customization made to this app's Web Form(s) through
	the Desk's own visual editor got silently reverted back to whatever
	the committed file says, the very next migrate. Now only ever runs
	once — a site's own later customization actually sticks. A brand-new
	site still gets every Web Form this app ships installed on its first
	migrate (patches run there too)."""
	web_form_dir = frappe.get_app_path("vendor_lifecycle", "vendor_lifecycle", "web_form")
	for path in glob.glob(os.path.join(web_form_dir, "*", "*.json")):
		import_file_by_path(path, force=True)


def after_install():
	# `bench install-app` never fires the `after_migrate` hook - that's a
	# separate hook that only runs during `bench migrate` - so every one of
	# this app's "create the default X if it's missing" seed functions
	# (email templates, rating/checklist templates, the Vendor KYC
	# Workflow, Indian states, etc.) lived only in after_migrate() and
	# never actually ran on a fresh install by itself, only on whatever
	# `bench migrate` a site happened to run afterward. Calling it here too
	# is safe - every one of those functions is already written to be a
	# no-op if its target already exists - so this just guarantees a fresh
	# install ends up fully seeded even if the site's own deployment
	# process never runs `bench migrate` as a separate step.
	#
	# Roles, Custom Fields, Property Setters, Supplier Connections and the
	# ERPNext permissions go in first (see vendor_lifecycle/setup.py) - the
	# Vendor KYC Workflow and later seeds need the roles to exist.
	#
	# Frappe marks every patch in patches.txt as already done on a fresh
	# install without running it, so the default records the seed_*_once
	# patches create for existing sites are created here instead
	# (seed_defaults). Settings defaults come first: some seeds save
	# Settings, which validates its mandatory fields.
	from vendor_lifecycle.setup import install

	install()
	after_migrate()
	seed_defaults()


# There used to be a resync_default_email_template_styling() here, run on
# every migrate, that deleted any default Email Template still missing a
# style marker so the (then-every-migrate) backfill_default_*_email_
# template*() calls right after it would recreate it freshly restyled.
# Once those became a one-time patch (see patches/seed_email_templates_
# once.py), that delete-then-recreate handoff broke — a template still
# missing the marker would be deleted with nothing left to bring it back.
# Removed rather than reworked: forcing a re-style by deleting the
# existing record is the same "overwrite a deliberate customization"
# problem this whole round of fixes was about — a site that had edited
# its own copy of a template would have had it silently replaced too.


def after_migrate():
	from vendor_lifecycle.setup import create_missing

	create_missing()
	sync_standard_files()
	backfill_settings_defaults()


def seed_defaults():
	"""Every default record the app ships — masters and templates, Email
	Templates, the Vendor KYC / Reboarding Request Workflows, the
	sampling-mandatory Business Types and the Form Tours. Run once, from
	after_install only (never on migrate), in the same order as the
	seed_*_once patches that created them on sites installed earlier. Each
	function skips anything that already exists."""
	_ensure_compliance_check_types()
	_ensure_default_compliance_check_template()
	_ensure_default_checklist_template()
	backfill_default_satisfaction_rating_template()
	backfill_default_compliance_check_sources()
	backfill_default_coverage_types()
	backfill_default_licenses_and_permits_masters()
	backfill_default_insurance_masters()
	backfill_default_rating_criteria_template()
	backfill_default_sampling_evaluation_template()
	seed_indian_states()
	backfill_default_deboarding_rating_template()
	backfill_default_deboarding_checklist_template()

	backfill_default_signoff_email_template()
	backfill_default_signoff_received_email_template()
	backfill_default_signoff_passed_email_template()
	backfill_default_signoff_failed_email_template()
	backfill_default_vendor_lifecycle_stage_email_templates()
	backfill_default_satisfaction_survey_email_templates()
	backfill_default_deboarding_request_email_templates()
	backfill_default_checklist_task_email_templates()
	backfill_default_clearance_certificate_email_templates()
	backfill_default_signoff_followup_email_template()
	backfill_default_manual_attach_needed_email_template()
	backfill_default_reboarding_request_email_templates()
	backfill_default_reboarding_request_creator_email_templates()
	backfill_default_compliance_audit_renewal_email_templates()
	backfill_default_signoff_renewal_email_templates()
	backfill_default_attachment_save_failed_email_template()

	install_vendor_kyc_workflow()
	install_vendor_reboarding_request_workflow()
	backfill_sampling_mandatory_business_types()
	_install_sample_form_tours()


# Business Types considered "service nature", plus "Other" (too ambiguous to
# default one way or the other), are intentionally excluded — Sampling
# Evaluation stays not-mandatory for them unless someone adds a row for them
# manually (being listed in the table IS being mandatory — there's no
# per-row toggle). Every other Business Type is seeded here as mandatory,
# since the flat "Sampling Evaluation Mandatory" checkbox this replaced
# defaulted to checked (mandatory) for everyone.
SAMPLING_MANDATORY_BUSINESS_TYPES = [
	"Manufacturer",
	"Trader",
	"Raw Material Supplier",
	"Equipment / Machinery Supplier",
	"Technology / Software Vendor",
]


def backfill_sampling_mandatory_business_types():
	# One-time seed of the Business Type table that replaced the flat
	# "Sampling Evaluation Mandatory" checkbox. Only inserts a row for a
	# Business Type that isn't already present — never touches a row a site
	# already has (whether seeded by an earlier run of this function, or
	# added/edited by hand), so this stays safe to run on every migrate.
	settings = frappe.get_single("Vendor Lifecycle Settings")
	existing = {row.business_type for row in settings.sampling_mandatory_overrides}
	missing = [bt for bt in SAMPLING_MANDATORY_BUSINESS_TYPES if bt not in existing]
	if not missing:
		return
	for business_type in missing:
		settings.append("sampling_mandatory_overrides", {"business_type": business_type})
	# This only ever touches sampling_mandatory_overrides - ignore_mandatory
	# so a full save() here can't fail on some other, unrelated mandatory
	# field on this same Settings singleton that a later step in
	# after_migrate() hasn't backfilled yet (this bit a fresh install once
	# already: default_deboarding_rating_template/default_checklist_template
	# weren't set yet at this point in the sequence).
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)


# Sample/prototype only, proving out the Getting Started mechanism before
# committing to writing real step content for every area of the app.
# "Vendor Lifecycle Onboarding" (Module Onboarding) drives the floating
# "Getting Started" panel - shared/global completion across every user,
# triggered via workspace_sidebar/vendor_lifecycle.json's own
# module_onboarding field (set on the source file, so sync_standard_files
# above already re-syncs it on every migrate). The two Form Tours are only
# ever launched by a user clicking a step in that panel - not ui_tour=1,
# which would auto-launch them uninvited the instant someone opens a
# matching page, and keep doing so on every visit until dismissed. That
# also means these two don't auto-chain into each other (Frappe only
# supports that via the ui_tour=1 auto-trigger path) - each is independent.
VENDOR_LIFECYCLE_FORM_TOURS = [
	"Vendor KYC Tour",
	"Vendor Onboarding Request Tour",
	"Vendor Deboarding Request Tour",
	"Vendor Deboarding Checklist Tour",
	"Vendor Satisfaction Survey Tour",
]


def backfill_form_tour_step_positions():
	# Every step in these tours was originally created with position="Left"
	# - fine for a field with room to its left, but for a field sitting
	# close to the left edge of the content area (e.g. Business Type, the
	# first field in its own section) the tooltip renders mostly off the
	# left edge of the screen, clipped and unreadable (confirmed via a
	# real screenshot). "Bottom" is Frappe's own default position for
	# exactly this reason (see Form Tour Step's own "position" field
	# default) and has no equivalent edge-clipping risk.
	# _install_sample_form_tours() below only ever creates these once
	# (a frappe.db.exists check) - a site that already has them installed
	# needs this explicit one-time fix instead of a fresh create. Runs
	# every migrate, but only ever touches a step still set to "Left".
	for tour_name in VENDOR_LIFECYCLE_FORM_TOURS:
		if not frappe.db.exists("Form Tour", tour_name):
			continue
		frappe.db.sql(
			"update `tabForm Tour Step` set position = 'Bottom' where parent = %s and position = 'Left'",
			(tour_name,),
		)


# install_getting_started_sample()/_install_sample_module_onboarding() used
# to live here — they manually inserted the sample Onboarding Step/Module
# Onboarding records "if not already there." Removed: both doctypes have
# real committed JSON files (module_onboarding/, onboarding_step/), which
# Frappe's own core file-sync already recreates on every migrate regardless
# of anything in this app's own code — confirmed directly in Frappe's own
# frappe/model/sync.py, which lists both doctypes among the ones it syncs
# the same way it does a DocType or Report. This function was pure
# redundant work riding alongside that.
#
# _install_sample_form_tours()/backfill_form_tour_step_positions() below
# are NOT part of that same file-backed sync — Form Tour has no committed
# JSON file backing it, so they used to re-check and recreate themselves on
# every migrate the same way the master data did. Moved to their own
# one-time patch instead (see patches/seed_form_tours_once.py).
def _install_sample_form_tours():
	# Deliberately NOT ui_tour=1 - that route-auto-triggers a tour the
	# instant a user lands on a matching page (and keeps re-triggering on
	# every visit until completed/skipped), which is exactly the unprompted,
	# repeatedly-interrupting behavior we decided against. These are only
	# ever launched by a user actually clicking "Take the Onboarding Tour"
	# in the Getting Started panel (see the Onboarding Step below) - opt-in,
	# once, never uninvited. The trade-off: Frappe only supports auto-
	# chaining one tour into the next (next_form_tour) via that same
	# ui_tour=1 auto-trigger mechanism, so these two tours no longer chain
	# into each other - each is independent, launched on its own.
	if not frappe.db.exists("Form Tour", "Vendor KYC Tour"):
		frappe.get_doc({
			"doctype": "Form Tour",
			"title": "Vendor KYC Tour",
			"reference_doctype": "Vendor KYC",
			"save_on_complete": 0,
			# Every mandatory field gets its own step, plus a handful of
			# section overviews (anchored on a field that's always in the
			# DOM on a fresh document - same depends_on trap as the
			# Onboarding Request tour: Business Details' own fields are all
			# gated on business_type, so that tab is only mentioned via the
			# Business Type step, not its own step).
			"steps": [
				{
					"title": "Verification",
					"fieldname": "verified_by_external_agency",
					"fieldtype": "Check",
					"label": "Verified by External Agency",
					"description": "Either pick internal verifiers below, or tick this to hand verification to an outside agency instead.",
					"position": "Bottom",
				},
				{
					"title": "Firm Name",
					"fieldname": "firm_name",
					"fieldtype": "Data",
					"label": "Firm Name",
					"description": "Carried over from the Onboarding Request - double-check it's correct. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Legal Entity Type",
					"fieldname": "legal_entity_type",
					"fieldtype": "Select",
					"label": "Legal Entity Type",
					"description": "How this vendor is legally structured - Sole Proprietorship, Private Limited, Partnership, and so on. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Billing Currency",
					"fieldname": "billing_currency",
					"fieldtype": "Link",
					"label": "Billing Currency",
					"description": "The Supplier Setup section - which currency this vendor is billed and paid in.",
					"position": "Bottom",
				},
				{
					"title": "Contact Person Name",
					"fieldname": "contact_person_name",
					"fieldtype": "Data",
					"label": "Contact Person Name",
					"description": "The Contact tab - who to reach at this vendor for day-to-day communication. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Contact Person Number",
					"fieldname": "contact_person_number",
					"fieldtype": "Phone",
					"label": "Contact Person Number",
					"description": "A direct phone number for the Contact Person above. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Official Email",
					"fieldname": "official_email",
					"fieldtype": "Data",
					"label": "Official Email",
					"description": "Where every KYC-related email to this vendor goes. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Address Line 1",
					"fieldname": "address_line_1",
					"fieldtype": "Data",
					"label": "Address Line 1",
					"description": "The Address tab - the first line of this vendor's full postal address, used for the GSTIN checks further on for India-based vendors. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "City",
					"fieldname": "city",
					"fieldtype": "Data",
					"label": "City",
					"description": "The city this vendor's registered address is in. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Country",
					"fieldname": "country",
					"fieldtype": "Link",
					"label": "Country",
					"description": "Mandatory - also decides whether the India-specific PAN/GSTIN fields on the Tax & Compliance tab show up.",
					"position": "Bottom",
				},
				{
					"title": "Postal / ZIP Code",
					"fieldname": "pincode",
					"fieldtype": "Data",
					"label": "Postal / ZIP Code",
					"description": "The postal or ZIP code for the address above. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Business Type",
					"fieldname": "business_type",
					"fieldtype": "Select",
					"label": "Business Type",
					"description": "The Business Details tab - once you pick a Business Type, extra fields specific to that type of vendor appear below it. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Tax & Compliance",
					"fieldname": "tax_id",
					"fieldtype": "Data",
					"label": "Tax ID (PAN / VAT / EIN, etc.)",
					"description": "PAN, GSTIN, business registration, and compliance certificates all live on this tab.",
					"position": "Bottom",
				},
				{
					"title": "Bank Details",
					"fieldname": "bank_account_name",
					"fieldtype": "Data",
					"label": "Bank Account Name",
					"description": "Needed before any payment can be made to this vendor.",
					"position": "Bottom",
				},
			],
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Form Tour", "Vendor Onboarding Request Tour"):
		frappe.get_doc({
			"doctype": "Form Tour",
			"title": "Vendor Onboarding Request Tour",
			"reference_doctype": "Vendor Onboarding Request",
			"save_on_complete": 0,
			# 2 standout individual fields + 2 section overviews (anchored on
			# each section's first real field, since the tour engine can
			# only spotlight actual field elements, not a Section Break
			# itself) - covers the whole form's structure without going
			# field-by-field through all ~50 of them.
			#
			# "About Your Business" is deliberately not its own step here -
			# every single field in that section has depends_on: business_type
			# (they only render once a Business Type is picked), so there's
			# no field in it that's reliably in the DOM for the tour to
			# anchor on. Mentioned in the Business Type step's own
			# description instead.
			"steps": [
				{
					"title": "Company / Firm Name",
					"fieldname": "company_name",
					"fieldtype": "Data",
					"label": "Company / Firm Name",
					"description": "The vendor's registered company or firm name.",
					"position": "Bottom",
				},
				{
					"title": "Business Type",
					"fieldname": "business_type",
					"fieldtype": "Select",
					"label": "Business Type",
					"description": "What the vendor does - once you pick one, an \"About Your Business\" section appears further down with extra fields specific to that type of vendor.",
					"position": "Bottom",
				},
				{
					"title": "Contact & Address",
					"fieldname": "contact_person",
					"fieldtype": "Data",
					"label": "Contact Person",
					"description": "This section covers the vendor's basic reachability - Contact Person, Phone, Email, and full Address.",
					"position": "Bottom",
				},
				{
					"title": "Profile & Catalogue",
					"fieldname": "profile_attachment",
					"fieldtype": "Attach",
					"label": "Business / Capability Profile",
					"description": "Optional supporting documents - a company profile, product catalogue, or anything else worth attaching.",
					"position": "Bottom",
				},
			],
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Form Tour", "Vendor Deboarding Request Tour"):
		frappe.get_doc({
			"doctype": "Form Tour",
			"title": "Vendor Deboarding Request Tour",
			"reference_doctype": "Vendor Deboarding Request",
			"save_on_complete": 0,
			# No step for the "Open Transactions" HTML display - same
			# depends_on-style trap as elsewhere: its content is only ever
			# populated by JS after the document is saved with a real
			# vendor (`if (frm.is_new()) return;` in this doctype's own
			# .js), so it's empty on the fresh document the tour runs on.
			"steps": [
				{
					"title": "Vendor",
					"fieldname": "vendor",
					"fieldtype": "Link",
					"label": "Vendor",
					"description": "Who's being deboarded. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Reason",
					"fieldname": "reason",
					"fieldtype": "Small Text",
					"label": "Reason",
					"description": "Why this vendor is being deboarded. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Is the Issue Resolvable?",
					"fieldname": "is_resolvable",
					"fieldtype": "Select",
					"label": "Is the Issue with the Vendor Resolvable?",
					"description": "A judgment call on whether this could still be fixed rather than ending the relationship. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Ratings",
					"fieldname": "ratings",
					"fieldtype": "Table",
					"label": "Ratings",
					"description": "Score every row here before this request can be saved - a final performance record for this vendor.",
					"position": "Bottom",
				},
			],
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Form Tour", "Vendor Deboarding Checklist Tour"):
		frappe.get_doc({
			"doctype": "Form Tour",
			"title": "Vendor Deboarding Checklist Tour",
			"reference_doctype": "Vendor Deboarding Checklist",
			"save_on_complete": 0,
			"steps": [
				{
					"title": "Checklist Template",
					"fieldname": "checklist_template",
					"fieldtype": "Link",
					"label": "Checklist Template",
					"description": "Which template this Checklist's tasks were loaded from. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Company",
					"fieldname": "company",
					"fieldtype": "Link",
					"label": "Company",
					"description": "Which Company this Checklist belongs to - used to resolve the GSTIN shown on the clearance certificate email.",
					"position": "Bottom",
				},
				{
					"title": "Additional Email",
					"fieldname": "additional_email",
					"fieldtype": "Data",
					"label": "Additional Supplier Email",
					"description": "An extra recipient for the clearance certificate email, alongside the vendor's own KYC/Supplier contacts.",
					"position": "Bottom",
				},
				{
					"title": "Checklist Items",
					"fieldname": "checklist_items",
					"fieldtype": "Table",
					"label": "Checklist Items",
					"description": "Every task needs a status (Completed/Invalid/Unable to Complete) and at least one assignee before this Checklist can be submitted.",
					"position": "Bottom",
				},
				{
					"title": "Clearance",
					"fieldname": "no_clearance_certificate",
					"fieldtype": "Check",
					"label": "No Clearance Certificate Available",
					"description": "Attach the signed clearance certificate below, or tick this and give a reason if one genuinely isn't available.",
					"position": "Bottom",
				},
			],
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Form Tour", "Vendor Satisfaction Survey Tour"):
		frappe.get_doc({
			"doctype": "Form Tour",
			"title": "Vendor Satisfaction Survey Tour",
			"reference_doctype": "Vendor Satisfaction Survey",
			"save_on_complete": 0,
			"steps": [
				{
					"title": "Vendor",
					"fieldname": "vendor",
					"fieldtype": "Link",
					"label": "Vendor",
					"description": "Which vendor this survey is scoring. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Period",
					"fieldname": "period",
					"fieldtype": "Select",
					"label": "Period",
					"description": "Which period this survey covers. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Survey Date",
					"fieldname": "survey_date",
					"fieldtype": "Date",
					"label": "Survey Date",
					"description": "When this survey was conducted. Mandatory.",
					"position": "Bottom",
				},
				{
					"title": "Ratings",
					"fieldname": "ratings",
					"fieldtype": "Table",
					"label": "Ratings",
					"description": "Score every criteria row here - the actual satisfaction scoring for this vendor.",
					"position": "Bottom",
				},
				{
					"title": "Feedback",
					"fieldname": "issues",
					"fieldtype": "Small Text",
					"label": "Areas of Concern",
					"description": "The Feedback section - Areas of Concern and Suggestions for Improvement, both optional free text.",
					"position": "Bottom",
				},
			],
		}).insert(ignore_permissions=True)


# There used to be a migrate_reference_check_mandatory_setting() here, a
# one-time carry-over for an old "reference_check_mandatory" Settings
# checkbox from back when this app had a separate "Vendor Reference Check"
# doctype (long since renamed/merged into Vendor Background Check).
# Removed: its own existence check used the same broken frappe.db.exists(
# "Singles", {...}) pattern found and fixed elsewhere in this file, so it
# had never actually run on any site. Confirmed safe to just delete rather
# than fix - the old field has zero rows in the database, the doctype it
# was about doesn't exist anymore, and the new field (background_check_
# mandatory) already gets a correct value through the unrelated, already-
# fixed backfill_settings_defaults() instead.


# Vendor KYC's own status field (see vendor_kyc.json) drives, and is driven
# by, this Workflow. A Vendor Lifecycle User does the actual KYC work and
# submits it for review ("Send for Approval": In Progress -> Approval
# Pending); everything from there on — Approve, Reject, Cancel — is a
# Vendor Lifecycle Manager's call. In Progress/Approval Pending/Rejected are
# docstatus 0 (Draft), Approved is docstatus 1 (Submitted, via the
# Workflow's own "Approve" action calling doc.submit() - on_submit() still
# runs exactly as before, Supplier creation included), Cancelled is
# docstatus 2 (via "Cancel" calling doc.cancel()). "Approved", "Rejected"
# and "Cancelled" - and the "Send for Approval"/"Approve"/"Reject"/"Cancel"
# actions - are all standard Frappe fixtures already present on any fresh
# site; only "In Progress"/"Approval Pending" (the states) and the Workflow
# itself are genuinely new here, and even those are only created if not
# already there.
VENDOR_KYC_WORKFLOW_STATES = {
	# state: (doc_status, allow_edit role, Workflow State style)
	"In Progress": ("0", "Vendor Lifecycle User", "Warning"),
	"Approval Pending": ("0", "Vendor Lifecycle Manager", "Info"),
	"Approved": ("1", "Vendor Lifecycle Manager", None),  # reused as-is - already styled Success
	"Rejected": ("0", "Vendor Lifecycle Manager", None),  # reused as-is - already styled Danger
	"Cancelled": ("2", "Vendor Lifecycle Manager", None),  # reused as-is
	# A User can discard an abandoned/mistaken KYC before ever sending it
	# for approval; only a Manager can then edit it further (enforced by
	# Frappe's own Workflow permission engine via allow_edit, no extra
	# code needed) - a terminal state, same as Rejected, not a bounce-back.
	"Trashed": ("0", "Vendor Lifecycle Manager", "Inverse"),
}
# (state, action, next_state, allowed role) - one role per transition, per
# the actual review split: only a User can send for approval, only a
# Manager can decide from there.
VENDOR_KYC_WORKFLOW_TRANSITIONS = [
	("In Progress", "Send for Approval", "Approval Pending", "Vendor Lifecycle User"),
	("Approval Pending", "Approve", "Approved", "Vendor Lifecycle Manager"),
	("Approval Pending", "Reject", "Rejected", "Vendor Lifecycle Manager"),
	("Approved", "Cancel", "Cancelled", "Vendor Lifecycle Manager"),
	("In Progress", "Trash", "Trashed", "Vendor Lifecycle User"),
]

# Same shape as Vendor KYC's own workflow above, for Vendor Reboarding
# Request - deliberately its own separate spec, not a shared constant,
# consistent with keeping Re-boarding's own settings/logic independent of
# the onboarding pipeline's (see the Re-boarding feature's own design
# notes) - only the generic installer plumbing below is actually shared.
VENDOR_REBOARDING_REQUEST_WORKFLOW_STATES = {
	"In Progress": ("0", "Vendor Lifecycle User", "Warning"),
	"Approval Pending": ("0", "Vendor Lifecycle Manager", "Info"),
	"Approved": ("1", "Vendor Lifecycle Manager", None),
	"Rejected": ("0", "Vendor Lifecycle Manager", None),
	"Cancelled": ("2", "Vendor Lifecycle Manager", None),
	"Trashed": ("0", "Vendor Lifecycle Manager", "Inverse"),
}
VENDOR_REBOARDING_REQUEST_WORKFLOW_TRANSITIONS = [
	("In Progress", "Send for Approval", "Approval Pending", "Vendor Lifecycle User"),
	("Approval Pending", "Approve", "Approved", "Vendor Lifecycle Manager"),
	("Approval Pending", "Reject", "Rejected", "Vendor Lifecycle Manager"),
	("Approved", "Cancel", "Cancelled", "Vendor Lifecycle Manager"),
	("In Progress", "Trash", "Trashed", "Vendor Lifecycle User"),
]


def _install_workflow(document_type, states, transitions):
	"""Shared installer for any (states, transitions) spec shaped like
	VENDOR_KYC_WORKFLOW_STATES/_TRANSITIONS above - generic plumbing
	(create missing Workflow State/Workflow Action Master fixtures,
	diff-and-resync the Workflow document itself), not a business rule,
	so sharing it doesn't conflict with keeping each doctype's own
	mandatory-check settings/logic independent."""
	for state, (_doc_status, _allow_edit, style) in states.items():
		if frappe.db.exists("Workflow State", state):
			continue
		frappe.get_doc({
			"doctype": "Workflow State",
			"workflow_state_name": state,
			"style": style,
		}).insert(ignore_permissions=True)

	# A fresh site only ships 3 default Workflow Action Master records
	# (Approve/Reject/Review, seeded by frappe/utils/install.py) - any other
	# action name, like our own "Send for Approval"/"Cancel"/"Trash",
	# normally only gets created as a side effect of typing it into the
	# Workflow Builder UI. Since this Workflow is created here in code,
	# never through that UI, create whichever ones are missing ourselves
	# first - otherwise the Workflow Transition rows below fail link
	# validation on a truly fresh install.
	actions = {action for _state, action, _next_state, _allowed in transitions}
	for action in actions:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({
				"doctype": "Workflow Action Master",
				"workflow_action_name": action,
			}).insert(ignore_permissions=True)

	if frappe.db.exists("Workflow", document_type):
		workflow = frappe.get_doc("Workflow", document_type)
	else:
		workflow = frappe.new_doc("Workflow")
		workflow.workflow_name = document_type
		workflow.document_type = document_type
		workflow.is_active = 1
		workflow.send_email_alert = 0

	# Frappe's own default ("workflow_state") - not the app's existing
	# "status" Select field. Set explicitly (rather than left blank) so
	# it's unambiguous in code, but it's the same value Frappe would use
	# on its own; Frappe auto-creates its usual hidden Link-to-Workflow-
	# State field for it the first time this is saved. The app's own
	# status field keeps tracking the same states exactly as it already
	# did (on_submit/on_cancel/reject) - untouched by this; the two just
	# happen to always agree, tracked independently.
	state_field_changed = workflow.workflow_state_field != "workflow_state"
	workflow.workflow_state_field = "workflow_state"

	# Rebuilt from the spec above every time rather than diffed - this is
	# the one workflow each of these doctypes installs, so there's
	# nothing a site admin could have customized on it yet to preserve;
	# simplest way to keep it in sync as the spec itself changes (e.g.
	# adding a new state here).
	current_states = [
		(s.state, s.doc_status, s.allow_edit) for s in workflow.states
	]
	desired_states = [
		(state, doc_status, allow_edit)
		for state, (doc_status, allow_edit, _style) in states.items()
	]
	current_transitions = [
		(t.state, t.action, t.next_state, t.allowed) for t in workflow.transitions
	]
	if (
		current_states == desired_states
		and current_transitions == transitions
		and not state_field_changed
	):
		return

	workflow.set("states", [])
	workflow.set("transitions", [])
	for state, (doc_status, allow_edit, _style) in states.items():
		workflow.append("states", {
			"state": state,
			"doc_status": doc_status,
			"allow_edit": allow_edit,
		})
	for state, action, next_state, allowed in transitions:
		workflow.append("transitions", {
			"state": state,
			"action": action,
			"next_state": next_state,
			"allowed": allowed,
		})

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)


def install_vendor_kyc_workflow():
	_install_workflow("Vendor KYC", VENDOR_KYC_WORKFLOW_STATES, VENDOR_KYC_WORKFLOW_TRANSITIONS)


def install_vendor_reboarding_request_workflow():
	_install_workflow(
		"Vendor Reboarding Request",
		VENDOR_REBOARDING_REQUEST_WORKFLOW_STATES,
		VENDOR_REBOARDING_REQUEST_WORKFLOW_TRANSITIONS,
	)


COMPLIANCE_CHECK_TYPES = [
	("Background Check", "General background verification outcome."),
	("Sanctions / PEP Screening", "Screening against sanctions and politically-exposed-person lists (e.g. OFAC, UN, EU)."),
	("Criminal / Court Record Check", "Criminal or court record search — source varies by country."),
	("Credit / Financial Standing Check", "Credit/financial standing check via any bureau (e.g. CIBIL, Experian, Equifax)."),
	("Regulatory Debarment / Blacklist Check", "Check against regulatory debarment or blacklist registries."),
	("Adverse Media / Negative News Check", "Search for adverse media or negative news coverage of the vendor."),
	("Litigation History Check", "Civil litigation / legal dispute history, distinct from a criminal record check."),
	("Address / Site Verification", "Verification that the vendor's registered address or site is genuine."),
	("Bank Account Verification", "Verification that the vendor's declared bank account belongs to them."),
	("UBO Verification", "Independent verification of the vendor's declared Ultimate Beneficial Owner(s)."),
]


def _ensure_compliance_check_types():
	for name, description in COMPLIANCE_CHECK_TYPES:
		if not frappe.db.exists("Compliance Check Type", name):
			frappe.get_doc({
				"doctype": "Compliance Check Type",
				"check_type_name": name,
				"description": description,
			}).insert(ignore_permissions=True)


DEFAULT_COMPLIANCE_CHECK_TEMPLATE = "Standard Compliance Checks"


def _ensure_default_compliance_check_template():
	# Compliance Check Template is mandatory on Vendor Background Check, so
	# there needs to be at least one usable template the moment that field
	# exists — otherwise a fresh install (or this very upgrade) would leave
	# the field mandatory-but-unselectable. Only created once; a deployment
	# is free to edit its contents, disable it, or add other templates
	# afterward.
	if frappe.db.exists("Compliance Check Template", DEFAULT_COMPLIANCE_CHECK_TEMPLATE):
		return
	frappe.get_doc({
		"doctype": "Compliance Check Template",
		"template_name": DEFAULT_COMPLIANCE_CHECK_TEMPLATE,
		"description": "All standard check types. Copy this and remove rows to make a narrower template.",
		"items": [{"check_type": name} for name, _description in COMPLIANCE_CHECK_TYPES],
	}).insert(ignore_permissions=True)


SETTINGS_FIELD_DEFAULTS = {
	"enable_satisfaction_surveys": 1,
	"survey_cadence": "Quarterly",
	"reboarding_request_emails": 1,
	"duplicate_request_handling": "Warn",
	"require_onboarding_request_for_kyc": 1,
	"duplicate_kyc_handling": "Stop",
	"require_complete_bank_details": 1,
	"require_complete_address_details": 1,
	"require_pan_card": 1,
	"require_tax_id": 0,
	"temporary_enable_days": 7,
	"background_check_mandatory": 1,
	"audit_mandatory": 1,
	"reference_result_method": "Average",
	"reference_minimum_average_rating": 3,
	"background_check_result_method": "Each Reference Must Pass",
	"compliance_audit_validity_months": 12,
	"onboarding_request_emails": 1,
	"onboarding_verification_emails": 1,
	"deboarding_request_emails": 1,
	"deboarding_checklist_emails": 1,
	"deboarding_checklist_task_reminder_emails": 1,
	"deboarding_checklist_clearance_followup_emails": 1,
	"satisfaction_survey_emails": 1,
	"satisfaction_survey_reminder_emails": 1,
	"compliance_audit_renewal_due_emails": 1,
	"compliance_audit_renewal_draft_reminder_emails": 1,
	"signoff_renewal_due_emails": 1,
	"signoff_renewal_draft_reminder_emails": 1,
}


# backfill_supplier_compliance_audit_valid_until() / backfill_supplier_
# contract_valid_until() used to live here — one-time mirrors of "when
# does this vendor's Compliance Audit/contract currently expire" onto the
# Supplier, for any vendor that already had a Passed record before these
# Supplier-level fields existed. Removed: every stage doctype that can set
# either field already pushes it directly on submit going forward (and
# each backfill only ever filled a currently-blank field), so nothing
# running today can still leave one of these blank — running this on
# every migrate was pure repeated work over already-settled data.


def backfill_settings_defaults():
	# A Single doctype's field default only applies when that field is first
	# created on a brand new site. On a site that already had "Vendor
	# Lifecycle Settings" before one of these fields existed, migrate adds
	# the column but leaves it empty — so fill it in explicitly, once.
	#
	# Checked by row existence in `tabSingles`, not by truthiness of the
	# value: a Check field backfilled to "1" is legitimately "0" once an
	# admin unchecks it, and a falsy-value check would keep flipping it
	# back to the default on every migrate.
	#
	# frappe.db.exists("Singles", {...}) does NOT work for this - Singles
	# is a special internal storage table, not a normal doctype, and that
	# call silently returns falsy even when a row genuinely exists.
	# Confirmed directly against the database: every field in this dict
	# was being unconditionally reset to its default on every single
	# migrate, silently undoing anything an admin had deliberately
	# changed. frappe.db.get_singles_dict() is what Frappe's own core
	# uses internally to load a Single doctype's real stored values -
	# checking key presence in that dict is the actual reliable way to
	# tell "never configured" apart from "deliberately set to 0/blank".
	existing_fields = frappe.db.get_singles_dict("Vendor Lifecycle Settings")
	for fieldname, default in SETTINGS_FIELD_DEFAULTS.items():
		if fieldname not in existing_fields:
			frappe.db.set_single_value("Vendor Lifecycle Settings", fieldname, default)


DEFAULT_SATISFACTION_RATING_TEMPLATE = "Default Rating Template"
DEFAULT_SATISFACTION_RATING_CRITERIA = [
	"Quality",
	"Timely Delivery",
	"Responsiveness",
	"Pricing Competitiveness",
	"Compliance & Documentation",
]


def backfill_default_satisfaction_rating_template():
	# default_rating_template is now mandatory on Vendor Lifecycle Settings
	# (a Satisfaction Survey can't be created at all without one resolving)
	# — a fresh install needs a real, usable template to point at out of
	# the box, not just a mandatory field left blank. Generic/industry-
	# neutral criteria, same as every other master in this app; a
	# deployment is free to edit or replace this template afterward, this
	# only ever fills it in once (never overwrites an existing choice).
	for criteria_name in DEFAULT_SATISFACTION_RATING_CRITERIA:
		if not frappe.db.exists("Rating Criteria", criteria_name):
			frappe.get_doc({"doctype": "Rating Criteria", "criteria_name": criteria_name}).insert(
				ignore_permissions=True
			)

	if not frappe.db.exists("Satisfaction Rating Template", DEFAULT_SATISFACTION_RATING_TEMPLATE):
		frappe.get_doc({
			"doctype": "Satisfaction Rating Template",
			"template_name": DEFAULT_SATISFACTION_RATING_TEMPLATE,
			"criteria": [{"criteria": name} for name in DEFAULT_SATISFACTION_RATING_CRITERIA],
		}).insert(ignore_permissions=True)

	if not frappe.db.get_single_value("Vendor Lifecycle Settings", "default_rating_template"):
		frappe.db.set_single_value(
			"Vendor Lifecycle Settings", "default_rating_template", DEFAULT_SATISFACTION_RATING_TEMPLATE
		)


# Generic, industry-neutral starter items — safety, legal, environmental, and
# labor basics that apply regardless of country or industry. Not meant to be
# exhaustive or authoritative for any specific sector; a deployment is
# expected to copy this template and adjust it for their own site/industry
# requirements, the same way Compliance Check Template's own seeded default
# is described.
DEFAULT_CHECKLIST_ITEMS = [
	("Fire Safety Equipment Present and Accessible", "Safety"),
	("Emergency Exits Clearly Marked and Unobstructed", "Safety"),
	("First Aid Kit Available and Stocked", "Safety"),
	("Business License / Registration Available", "Legal"),
	("Health and Safety Policy in Place", "Legal"),
	("Environmental Compliance Documentation Available", "Environmental"),
	("Waste Management and Disposal Compliant", "Environmental"),
	("Working Conditions Safe and Sanitary", "Labor"),
	("Wage and Labor Records Available", "Labor"),
	("Anti-Discrimination / Harassment Policy in Place", "Labor"),
]

DEFAULT_CHECKLIST_TEMPLATE = "Standard Facility Compliance Checklist"

DEFAULT_SIGNOFF_EMAIL_TEMPLATE = "Vendor Sign-off Request"
DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE = "Vendor Sign-off Document Received"
DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE = "Vendor Sign-off Passed"
DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE = "Vendor Sign-off Failed"


DEFAULT_COMPLIANCE_CHECK_SOURCES = [
	("Government Registry", "An official government-maintained registry or database."),
	("Credit Bureau", "A credit reporting agency (e.g. CIBIL, Experian, Equifax)."),
	("Sanctions / Watchlist Database", "A sanctions, PEP, or other watchlist database (e.g. OFAC, UN, EU)."),
	("Court Records Database", "A civil or criminal court records search."),
	("Media / News Search", "Adverse media or negative news coverage search."),
	("Third-Party Verification Agency", "An external agency engaged to carry out the check."),
]


def backfill_default_compliance_check_sources():
	# Compliance Check Source is picked on every Compliance Check row (see
	# vendor_background_check_compliance_item.json's own "source" field) —
	# unlike License Type/Issuing Authority/Insurance Type/Insurer above,
	# this one was never a free-text field with existing data to backfill
	# from, so a fresh install would otherwise leave it with nothing to
	# choose from at all. Generic/industry-neutral, same as every other
	# master in this app; only ever creates what's missing.
	for name, description in DEFAULT_COMPLIANCE_CHECK_SOURCES:
		if not frappe.db.exists("Compliance Check Source", name):
			frappe.get_doc({
				"doctype": "Compliance Check Source",
				"source_name": name,
				"description": description,
			}).insert(ignore_permissions=True)


DEFAULT_COVERAGE_TYPES = [
	("Fire", "Damage or loss caused by fire."),
	("Theft / Burglary", "Loss due to theft or burglary."),
	("Flood", "Damage or loss caused by flooding."),
	("Third-Party Liability", "Liability for injury or damage caused to a third party."),
	("Business Interruption", "Loss of income due to a covered disruption to operations."),
	("Employee Injury", "Injury or illness to an employee arising from their work."),
]


def backfill_default_coverage_types():
	# Picked on every Insurance Peril row under a Vendor Compliance Audit's
	# own Insurance table (see vendor_compliance_audit_insurance_peril.json)
	# — same "nothing to choose from on a fresh install" reasoning as
	# backfill_default_compliance_check_sources() above.
	for name, description in DEFAULT_COVERAGE_TYPES:
		if not frappe.db.exists("Coverage Type", name):
			frappe.get_doc({
				"doctype": "Coverage Type",
				"coverage_type_name": name,
				"description": description,
			}).insert(ignore_permissions=True)


DEFAULT_LICENSE_TYPES = [
	("Business Registration / Trade License", "The core license to legally operate as a business."),
	("Tax Registration Certificate", "Registration with the relevant tax authority (e.g. VAT/GST)."),
	("Environmental Clearance", "Clearance confirming compliance with environmental regulations."),
	("Fire Safety Certificate", "Certification of compliance with fire safety requirements."),
	("Import / Export License", "Authorization to import or export goods."),
	("Industry-Specific Operating License", "Any license specific to this vendor's own industry or trade."),
]

DEFAULT_ISSUING_AUTHORITIES = [
	("Local Municipal Authority", "The vendor's local city/municipal government body."),
	("National Tax Authority", "The country's central tax collection authority."),
	("Environmental Protection Agency", "The relevant environmental regulator."),
	("Fire Safety Department", "The local fire safety / fire department authority."),
	("Trade / Commerce Ministry", "The national ministry or department overseeing trade."),
	("Industry Regulatory Body", "Any regulator specific to this vendor's own industry."),
]

DEFAULT_LICENSES_AND_PERMITS_TEMPLATE = "Standard Licenses & Permits"


def backfill_default_licenses_and_permits_masters():
	# License Type and Issuing Authority are both mandatory Link fields on
	# every Vendor Compliance Audit License row — backfill_license_
	# insurance_masters() above only ever creates a record for a value
	# some *existing* row already used, so a genuinely fresh install with
	# no prior data would leave both completely empty. These generic
	# defaults, plus one ready-to-use template built from them, give a
	# fresh install something usable immediately — same "copy this and
	# remove rows" pattern as _ensure_default_compliance_check_template()
	# above. License Type must exist before the template's own rows
	# reference it, or those Link rows would fail to insert — hence the
	# explicit order within this one function.
	for name, description in DEFAULT_LICENSE_TYPES:
		if not frappe.db.exists("License Type", name):
			frappe.get_doc({
				"doctype": "License Type",
				"license_type_name": name,
				"description": description,
			}).insert(ignore_permissions=True)

	for name, description in DEFAULT_ISSUING_AUTHORITIES:
		if not frappe.db.exists("Issuing Authority", name):
			frappe.get_doc({
				"doctype": "Issuing Authority",
				"issuing_authority_name": name,
				"description": description,
			}).insert(ignore_permissions=True)

	if not frappe.db.exists("Licenses and Permits Template", DEFAULT_LICENSES_AND_PERMITS_TEMPLATE):
		frappe.get_doc({
			"doctype": "Licenses and Permits Template",
			"template_name": DEFAULT_LICENSES_AND_PERMITS_TEMPLATE,
			"description": "All standard license/permit types. Copy this and remove rows to make a narrower template.",
			"items": [{"license_type": name} for name, _description in DEFAULT_LICENSE_TYPES],
		}).insert(ignore_permissions=True)


DEFAULT_INSURANCE_TYPES = [
	("General Liability Insurance", "Broad coverage for third-party bodily injury or property damage claims."),
	("Product Liability Insurance", "Coverage for harm caused by a product the vendor supplies."),
	("Workers' Compensation Insurance", "Coverage for employee injury or illness arising from their work."),
	("Property Insurance", "Coverage for damage or loss to the vendor's own physical property."),
	("Cyber Liability Insurance", "Coverage for data breaches and other cyber-related incidents."),
	("Marine / Cargo Insurance", "Coverage for goods in transit by sea, air, or land."),
]

# Explicit product decision (2026-09-11): real, named insurers rather than
# generic scope-based categories — a deployment can freely add/remove its
# own beyond these.
DEFAULT_INSURERS = [
	("Life Insurance Corporation of India", "Life insurance provider."),
	("Tata AIG General Insurance", "General insurance provider."),
	("Star Health and Allied Insurance", "Health insurance provider."),
	("Berkshire Hathaway", "General/reinsurance provider."),
]

DEFAULT_INSURANCE_TEMPLATE = "Standard Insurance Coverage"


def backfill_default_insurance_masters():
	# Same reasoning as backfill_default_licenses_and_permits_masters()
	# above, for Insurance Type/Insurer/Insurance Template — Insurance
	# Type must exist before the template's own rows reference it.
	for name, description in DEFAULT_INSURANCE_TYPES:
		if not frappe.db.exists("Insurance Type", name):
			frappe.get_doc({
				"doctype": "Insurance Type",
				"insurance_type_name": name,
				"description": description,
			}).insert(ignore_permissions=True)

	for name, description in DEFAULT_INSURERS:
		if not frappe.db.exists("Insurer", name):
			frappe.get_doc({
				"doctype": "Insurer",
				"insurer_name": name,
				"description": description,
			}).insert(ignore_permissions=True)

	if not frappe.db.exists("Insurance Template", DEFAULT_INSURANCE_TEMPLATE):
		frappe.get_doc({
			"doctype": "Insurance Template",
			"template_name": DEFAULT_INSURANCE_TEMPLATE,
			"description": "All standard insurance types. Copy this and remove rows to make a narrower template.",
			"items": [{"insurance_type": name} for name, _description in DEFAULT_INSURANCE_TYPES],
		}).insert(ignore_permissions=True)


DEFAULT_RATING_CRITERIA_TEMPLATE = "Standard Reference Rating"


def backfill_default_rating_criteria_template():
	# Rating Criteria Template is picked on Vendor Background Check
	# Reference (scoring a vendor's own supplied reference) — reuses the
	# same generic Rating Criteria master DEFAULT_SATISFACTION_RATING_
	# CRITERIA already seeds for Satisfaction Surveys (see
	# backfill_default_satisfaction_rating_template() above), rather than
	# inventing a second, overlapping set of criteria names. Re-ensures
	# those same rows exist here too, defensively, in case this ever runs
	# before that function does.
	for criteria_name in DEFAULT_SATISFACTION_RATING_CRITERIA:
		if not frappe.db.exists("Rating Criteria", criteria_name):
			frappe.get_doc({"doctype": "Rating Criteria", "criteria_name": criteria_name}).insert(
				ignore_permissions=True
			)

	if not frappe.db.exists("Rating Criteria Template", DEFAULT_RATING_CRITERIA_TEMPLATE):
		frappe.get_doc({
			"doctype": "Rating Criteria Template",
			"template_name": DEFAULT_RATING_CRITERIA_TEMPLATE,
			"result_method": "Average",
			"minimum_average_rating": 3,
			"criteria": [{"criteria": name} for name in DEFAULT_SATISFACTION_RATING_CRITERIA],
		}).insert(ignore_permissions=True)


DEFAULT_SAMPLING_EVALUATION_CRITERIA = [
	("Sample Conformance to Specification", "Quality"),
	("Defect Rate Within Acceptable Limits", "Quality"),
	("Packaging Adequacy", "Quality"),
	("Labeling Accuracy", "Compliance"),
	("Sample Submitted On Time", "Logistics"),
]

DEFAULT_SAMPLING_EVALUATION_TEMPLATE = "Standard Sampling Evaluation"


def backfill_default_sampling_evaluation_template():
	# Sampling Evaluation Template Criteria rows are plain text (no master
	# doctype behind them — see sampling_evaluation_template_criteria.json)
	# so there's no separate master to seed first here, unlike every other
	# function in this section.
	if not frappe.db.exists("Sampling Evaluation Template", DEFAULT_SAMPLING_EVALUATION_TEMPLATE):
		frappe.get_doc({
			"doctype": "Sampling Evaluation Template",
			"template_name": DEFAULT_SAMPLING_EVALUATION_TEMPLATE,
			"description": "A generic starting point. Copy this and adjust criteria to match what's actually being sampled.",
			"criteria": [{"criteria": name, "category": category} for name, category in DEFAULT_SAMPLING_EVALUATION_CRITERIA],
		}).insert(ignore_permissions=True)


def seed_indian_states():
	# The State master (used to validate Vendor Compliance Audit's Valid In
	# States) needs at least India's official list out of the box, so
	# existing India-based validation keeps working exactly as before —
	# without this, every site would start with an empty State master and
	# any Indian state would fail validation until someone manually typed
	# all 36-odd states in first. Site admins can still import any other
	# country's states themselves (State has allow_import). Idempotent:
	# only ever creates what's missing.
	if "india_compliance" not in frappe.get_installed_apps():
		return

	from india_compliance.gst_india.constants import INDIAN_STATES

	for state_name in INDIAN_STATES:
		name = f"{state_name} (India)"
		if not frappe.db.exists("State", name):
			frappe.get_doc({"doctype": "State", "state_name": state_name, "country": "India"}).insert(
				ignore_permissions=True
			)


def _ensure_default_checklist_template():
	# checklist_template isn't mandatory on Vendor Compliance Audit (unlike
	# Compliance Check Template on Vendor Background Check), so this isn't
	# fixing a mandatory-but-unselectable field — it's making sure a fresh
	# install has at least one usable starting point rather than zero
	# templates to pick from. Only created once; a deployment is free to
	# edit its contents, disable it, or add other templates afterward.
	if frappe.db.exists("Audit Checklist Template", DEFAULT_CHECKLIST_TEMPLATE):
		return
	frappe.get_doc({
		"doctype": "Audit Checklist Template",
		"template_name": DEFAULT_CHECKLIST_TEMPLATE,
		"description": (
			"Generic starter checklist covering safety, legal, environmental, and labor basics common"
			" across industries. Copy this and adjust it for your own industry/site requirements."
		),
		"items": [{"item": item, "category": category} for item, category in DEFAULT_CHECKLIST_ITEMS],
	}).insert(ignore_permissions=True)


def _signoff_email_shell(accent_color, badge, heading, body_html):
	# Shared visual wrapper for every Vendor Lifecycle email template — a
	# light outer canvas, a colored heading band with a small uppercase
	# status badge, and a bordered, subtly-shadowed content card. Inline
	# styles throughout (not a <style> block), a system-font stack, and a
	# light background behind the card — all deliberate for looking
	# professional in the widest range of email clients, including
	# desktop Outlook (which ignores box-shadow/gradients gracefully
	# rather than breaking on them).
	return (
		'<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
		'background-color:#eef1f5;padding:32px 16px;">'
		'<div style="max-width:600px;margin:0 auto;">'
		f'<div style="background-color:{accent_color};color:#ffffff;padding:32px 32px 26px;border-radius:14px 14px 0 0;">'
		'<span style="display:inline-block;background-color:rgba(255,255,255,0.2);color:#ffffff;'
		'padding:5px 14px;border-radius:14px;font-size:11px;font-weight:700;letter-spacing:1px;'
		f'text-transform:uppercase;margin-bottom:14px;">{badge}</span>'
		f'<h1 style="margin:0;font-size:22px;font-weight:650;line-height:1.35;letter-spacing:-0.2px;">{heading}</h1>'
		"</div>"
		'<div style="border:1px solid #e5e8ec;border-top:none;border-radius:0 0 14px 14px;'
		'padding:30px 32px;background-color:#ffffff;color:#32383e;font-size:14px;line-height:1.65;'
		'box-shadow:0 2px 10px rgba(16,24,40,0.04);">'
		f"{body_html}"
		"</div>"
		'<div style="text-align:center;margin-top:22px;padding-top:16px;border-top:1px solid #dfe3e8;">'
		'<p style="color:#9aa4b0;font-size:12px;margin:0;">'
		"This is an automated message from the Vendor Lifecycle system."
		"</p>"
		"</div>"
		"</div>"
		"</div>"
	)


def _info_box(accent_color, content_html, background_color="#f5f7fa"):
	# Shared "callout" box for any block of key-value details inside an
	# email body — reference identifiers, a resolution note, a reason,
	# etc. Every Vendor Lifecycle template routes any such block through
	# here so they all look identical, and a single change here restyles
	# every one of them at once. background_color defaults to neutral
	# gray; failure/rejection reasons pass a red tint instead.
	return (
		f'<div style="background-color:{background_color};border-left:4px solid {accent_color};padding:16px 20px;'
		'margin:20px 0;border-radius:8px;font-size:13px;line-height:1.7;color:#3d4550;">'
		f"{content_html}"
		"</div>"
	)


def _reason_box(accent_color, label, content_html):
	# The red-tinted variant of _info_box used specifically for "why this
	# failed/was rejected" call-outs.
	return _info_box(
		accent_color,
		f'<div style="font-weight:600;margin-bottom:4px;">{label}</div>{content_html}',
		background_color="#fdecea",
	)


def _signoff_reference_box(accent_color):
	# Every reference/identifier the sending Company and the vendor's own
	# KYC carry — company/GSTIN was already here; PAN (both sides) and the
	# vendor's own GSTIN are exactly the same kind of variable and easy to
	# miss unless deliberately kept in one shared place every template
	# pulls from.
	return _info_box(
		accent_color,
		'<div style="font-weight:700;margin-bottom:6px;text-transform:uppercase;font-size:11px;'
		'letter-spacing:0.6px;color:#6b7684;">Reference Details</div>'
		"Vendor KYC Reference: {{ kyc }}<br>"
		"{% if onboarding_request %}Onboarding Request Reference: {{ onboarding_request }}<br>{% endif %}"
		"{% if company_gstin %}{{ company_name }} GSTIN: {{ company_gstin }}<br>{% endif %}"
		"{% if company_pan %}{{ company_name }} PAN: {{ company_pan }}<br>{% endif %}"
		"{% if vendor_gstin %}Vendor GSTIN: {{ vendor_gstin }}<br>{% endif %}"
		"{% if vendor_pan %}Vendor PAN: {{ vendor_pan }}<br>{% endif %}",
	)


SIGNOFF_REQUEST_ACCENT = "#2c5cc5"
SIGNOFF_RECEIVED_ACCENT = "#0f766e"
SIGNOFF_PASSED_ACCENT = "#1f9d55"
SIGNOFF_FAILED_ACCENT = "#c0392b"


def backfill_default_signoff_email_template():
	# Gives a fresh install a real template for the "Send Email" button on
	# Vendor Sign Off — referenced directly by name in vendor_sign_off.py
	# (DEFAULT_SIGNOFF_EMAIL_TEMPLATE there), not a Settings field, by
	# explicit product decision. Only created once; a deployment is free
	# to edit its wording afterward.
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>As part of your onboarding with <b>{{ company_name }}</b>, please review and sign the attached"
			" <b>{{ document_type }}</b>.</p>"
			f"{_signoff_reference_box(SIGNOFF_REQUEST_ACCENT)}"
			"{% if notes %}<p><b>Notes:</b> {{ notes }}</p>{% endif %}"
			"<p>Once signed, please reply directly to this email with the signed copy attached. Replying"
			" (rather than forwarding) helps us match your response automatically.</p>"
			"<p>Thank you for your cooperation.</p>"
			"<p>Best regards,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_EMAIL_TEMPLATE,
			"subject": "Action Required: Sign the {{ document_type }} — {{ firm_name }}",
			"response": _signoff_email_shell(
				SIGNOFF_REQUEST_ACCENT, "Action Required", "Document Ready for Your Signature", body
			),
		}).insert(ignore_permissions=True)


def backfill_default_signoff_received_email_template():
	# Sent to the document's Creator + Settings CC when the vendor's
	# signed document is received (by email reply or manual attach) — see
	# VendorSignOff.notify_document_received(). Internal/staff-facing, not
	# sent to the vendor, so the tone here is a short informational
	# confirmation rather than a formal letter.
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			'<p>The signed <b>{{ document_type }}</b> from <b>{{ firm_name }}</b> has been received and attached'
			' to <a href="{{ sign_off_link }}">{{ sign_off_name }}</a>.</p>'
			f"{_signoff_reference_box(SIGNOFF_RECEIVED_ACCENT)}"
			"<p>No further action is needed unless the document requires review.</p>"
			"<p>Vendor Lifecycle System</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE,
			"subject": "Signed {{ document_type }} Received — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_RECEIVED_ACCENT, "Received", "Signed Document Received", body),
		}).insert(ignore_permissions=True)


def backfill_default_signoff_passed_email_template():
	# Sent on submit when a Sign-off passes — see
	# VendorSignOff._send_outcome_email(). Same "only created once, never
	# overwrites an admin's own edits" convention as the Request template.
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>We are pleased to confirm that <b>{{ firm_name }}</b>'s onboarding with <b>{{ company_name }}</b>"
			" is now complete.</p>"
			f"{_signoff_reference_box(SIGNOFF_PASSED_ACCENT)}"
			"<p>Welcome aboard — we look forward to a successful partnership.</p>"
			"<p>Best regards,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE,
			"subject": "Welcome Aboard — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_PASSED_ACCENT, "Approved", "Onboarding Complete", body),
		}).insert(ignore_permissions=True)


def backfill_default_signoff_failed_email_template():
	# Sent on submit when a Sign-off is marked Failed — see
	# VendorSignOff._send_outcome_email(). failure_reason is passed into
	# the template context alongside everything else _build_email_context()
	# already provides.
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>After careful review, we are unable to complete {{ firm_name }}'s onboarding with"
			" <b>{{ company_name }}</b> at this time.</p>"
			"{% if failure_reason %}"
			f"{_reason_box(SIGNOFF_FAILED_ACCENT, 'Reason', '{{ failure_reason }}')}"
			"{% endif %}"
			f"{_signoff_reference_box(SIGNOFF_FAILED_ACCENT)}"
			"<p>If you would like to discuss this further, please contact {{ company_name }} directly.</p>"
			"<p>Regards,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE,
			"subject": "Update on Your Onboarding — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Not Approved", "Sign-off Update", body),
		}).insert(ignore_permissions=True)


# Shared across every Vendor Lifecycle doctype's own pass/fail outcome
# email (Onboarding Request approved, KYC completed, and each of
# Background Check / Compliance Audit / Sampling Evaluation's own
# passed-or-force-overridden / failed-or-rejected result) — by explicit
# product decision, one template per outcome shape reused everywhere,
# rather than a separate template per doctype. Referenced directly by
# name in each doctype's own send function — deliberately NOT a Settings
# field, so there's one thing to redesign instead of a dozen.
VENDOR_LIFECYCLE_INTERNAL_ACCENT = "#4b5563"
DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Passed"
DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Stage Failed"
DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE = "Vendor Onboarding Request Received"
DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE = "Vendor Onboarding Request - New Submission"


def backfill_default_vendor_lifecycle_stage_email_templates():
	# Only created once each; a deployment is free to edit their wording
	# afterward. No reference box here (unlike the Sign-off templates
	# above) — these are shared across doctypes that don't all have a KYC/
	# company/GSTIN context to show (Onboarding Request, in particular,
	# has none of that yet), so the body stays deliberately simple.
	if not frappe.db.exists("Email Template", DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>Good news — <b>{{ firm_name }}</b> has successfully cleared the <b>{{ stage_name }}</b> stage"
			" with {{ company_name }}.</p>"
			"{% if override_note %}<p>{{ override_note }}</p>{% endif %}"
			"{% if next_stage %}<p>Next up: <b>{{ next_stage }}</b>. Our team will contact you shortly.</p>{% endif %}"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE,
			"subject": "{{ stage_name }} Approved — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_PASSED_ACCENT, "Approved", "{{ stage_name }} Approved", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>We regret to inform you that <b>{{ firm_name }}</b>'s <b>{{ stage_name }}</b> could not be"
			" completed successfully.</p>"
			"{% if reason %}"
			f"{_reason_box(SIGNOFF_FAILED_ACCENT, 'Reason', '{{ reason }}')}"
			"{% endif %}"
			"<p>If you have questions, please contact {{ company_name }} directly.</p>"
			"<p>Regards,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE,
			"subject": "Update on {{ stage_name }} — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Not Approved", "{{ stage_name }} Update", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person }},</p>"
			"<p>Thank you — we've received the vendor onboarding request from"
			" <b>{{ vendor_company_name }}</b>.</p>"
			"<p>Our team will review it and get back to you soon.</p>"
			"<p>Regards,<br>Vendor Lifecycle Team</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE,
			"subject": "We've Received Your Onboarding Request — {{ vendor_company_name }}",
			"response": _signoff_email_shell(SIGNOFF_RECEIVED_ACCENT, "Received", "Request Received", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>A new Vendor Onboarding Request has been submitted by <b>{{ vendor_company_name }}</b>.</p>"
			"<p>Contact: {{ contact_person }} ({{ contact_number }}, {{ email }})</p>"
			"{% if gstin_uin %}<p>GSTIN/UIN: {{ gstin_uin }}</p>{% endif %}"
			'<p><a href="{{ record_link }}">View the request</a> and take further action.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE,
			"subject": "New Vendor Onboarding Request — {{ vendor_company_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "New Onboarding Request", body
			),
		}).insert(ignore_permissions=True)


DEFAULT_SATISFACTION_SURVEY_CREATED_EMAIL_TEMPLATE = "Vendor Satisfaction Survey Created"
DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE = "Vendor Satisfaction Survey Reminder"


def backfill_default_satisfaction_survey_email_templates():
	# Referenced directly by name in vendor_satisfaction_survey.py /
	# tasks.py, not a Settings field — same "hardcoded, backfilled once"
	# convention as the other Vendor Lifecycle stage templates above. A
	# deployment is free to edit either's wording afterward.
	if not frappe.db.exists("Email Template", DEFAULT_SATISFACTION_SURVEY_CREATED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>A new Satisfaction Survey (<b>{{ survey_name }}</b>, {{ period }}) has been created for"
			" <b>{{ firm_name }}</b> with {{ company_name }}.</p>"
			'<p><a href="{{ survey_link }}">Open the survey</a> to rate us and share your feedback.</p>'
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SATISFACTION_SURVEY_CREATED_EMAIL_TEMPLATE,
			"subject": "New Satisfaction Survey — {{ survey_name }}",
			"response": _signoff_email_shell(SIGNOFF_REQUEST_ACCENT, "Action Needed", "New Satisfaction Survey", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>This is a reminder that Satisfaction Survey <b>{{ survey_name }}</b> ({{ period }}) for"
			" <b>{{ firm_name }}</b> is still pending completion.</p>"
			'<p><a href="{{ survey_link }}">Open the survey</a> and submit it at your earliest convenience.</p>'
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE,
			"subject": "Reminder: Satisfaction Survey Pending — {{ survey_name }}",
			"response": _signoff_email_shell(VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reminder", "Survey Still Pending", body),
		}).insert(ignore_permissions=True)


# Dedicated to Deboarding, not shared with Satisfaction Survey's own
# Rating Criteria/Template — see the standing rule against reusing one
# feature's template/master doctype for another.
DEFAULT_DEBOARDING_RATING_TEMPLATE = "Default Deboarding Rating Template"
DEFAULT_DEBOARDING_RATING_CRITERIA = [
	"Vendor Behaviour",
	"Product / Service Quality",
	"Process Understanding",
]


def backfill_default_deboarding_rating_template():
	# default_deboarding_rating_template is mandatory on Vendor Lifecycle
	# Settings — a fresh install needs a real, usable template to point at
	# out of the box, not just a mandatory field left blank. Generic/
	# industry-neutral criteria, same as every other master in this app; a
	# deployment is free to edit or replace this template afterward, this
	# only ever fills it in once (never overwrites an existing choice).
	for criteria_name in DEFAULT_DEBOARDING_RATING_CRITERIA:
		if not frappe.db.exists("Deboarding Rating Criteria", criteria_name):
			frappe.get_doc({"doctype": "Deboarding Rating Criteria", "criteria_name": criteria_name}).insert(
				ignore_permissions=True
			)

	if not frappe.db.exists("Deboarding Rating Template", DEFAULT_DEBOARDING_RATING_TEMPLATE):
		frappe.get_doc({
			"doctype": "Deboarding Rating Template",
			"template_name": DEFAULT_DEBOARDING_RATING_TEMPLATE,
			"criteria": [{"criteria": name} for name in DEFAULT_DEBOARDING_RATING_CRITERIA],
		}).insert(ignore_permissions=True)

	if not frappe.db.get_single_value("Vendor Lifecycle Settings", "default_deboarding_rating_template"):
		frappe.db.set_single_value(
			"Vendor Lifecycle Settings", "default_deboarding_rating_template", DEFAULT_DEBOARDING_RATING_TEMPLATE
		)


DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE = "Vendor Deboarding Request Created"
DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE = "Vendor Deboarding Request Rejected"
DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE = "Vendor Deboarding Request Approved"


def backfill_default_deboarding_request_email_templates():
	# Referenced directly by name in vendor_deboarding_request.py, not a
	# Settings field — same "hardcoded, backfilled once" convention as
	# every other Vendor Lifecycle template. Created/Rejected are
	# internal-only (Vendor Lifecycle Manager / the request's own Creator);
	# Approved is the one and only vendor-facing email this doctype sends —
	# submitting the request is the actual "you're being deboarded"
	# decision.
	if not frappe.db.exists("Email Template", DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>A new deboarding request <b>{{ request_name }}</b> has been raised for <b>{{ firm_name }}</b>"
			" ({{ vendor }}).</p>"
			+ _info_box(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT,
				"Requested On: {{ request_date }}<br>"
				"Reason: {{ reason }}<br>"
				"Resolvable: {{ is_resolvable }}",
			)
			+ '<p><a href="{{ request_link }}">View the request</a> and proceed with the deboarding checklist.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE,
			"subject": "New Deboarding Request — {{ firm_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "New Deboarding Request", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>Deboarding request <b>{{ request_name }}</b> for <b>{{ firm_name }}</b> ({{ vendor }}) has been"
			" rejected and will not proceed.</p>"
			'<p><a href="{{ request_link }}">View the request</a> for details.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE,
			"subject": "Deboarding Request Rejected — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Rejected", "Deboarding Request Rejected", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>This is to inform you that {{ company_name }} has approved a deboarding request"
			" (<b>{{ request_name }}</b>) for <b>{{ firm_name }}</b>, effective {{ request_date }}.</p>"
			+ _info_box(SIGNOFF_FAILED_ACCENT, "Reason: {{ reason }}")
			+ "<p>Please coordinate with us to settle any pending transactions and complete the handover.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE,
			"subject": "Deboarding Request Approved — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Approved", "Deboarding Approved", body),
		}).insert(ignore_permissions=True)


DEFAULT_REBOARDING_REQUEST_RECEIVED_EMAIL_TEMPLATE = "Vendor Reboarding Request Received"
DEFAULT_REBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE = "Vendor Reboarding Request Approved"
DEFAULT_REBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE = "Vendor Reboarding Request Rejected"
DEFAULT_REBOARDING_REQUEST_APPROVED_CREATOR_EMAIL_TEMPLATE = "Vendor Reboarding Request Approved - Creator Copy"
DEFAULT_REBOARDING_REQUEST_REJECTED_CREATOR_EMAIL_TEMPLATE = "Vendor Reboarding Request Rejected - Creator Copy"


def backfill_default_reboarding_request_email_templates():
	# All three are vendor-facing (the supplier being reconsidered), unlike
	# Deboarding Request's own Created/Rejected (internal-only) - there's
	# no internal "request raised" email here since this is always staff-
	# initiated in the first place; the vendor only needs to hear about it
	# once it's actually under review.
	if not frappe.db.exists("Email Template", DEFAULT_REBOARDING_REQUEST_RECEIVED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or 'Sir/Madam' }},</p>"
			"<p>We have received a request to re-board your account with {{ company_name }}"
			" (<b>{{ request_name }}</b>), and it is now under review.</p>"
			+ _info_box(SIGNOFF_REQUEST_ACCENT, "Requested On: {{ request_date }}")
			+ "<p>We'll be in touch once a decision has been made.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_REBOARDING_REQUEST_RECEIVED_EMAIL_TEMPLATE,
			"subject": "Your Re-boarding Request Has Been Received",
			"response": _signoff_email_shell(SIGNOFF_REQUEST_ACCENT, "Received", "Re-boarding Request Received", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_REBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or 'Sir/Madam' }},</p>"
			"<p>We're pleased to let you know that your re-boarding request with {{ company_name }}"
			" (<b>{{ request_name }}</b>) has been approved.</p>"
			"<p>We'll be in touch shortly about next steps.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_REBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE,
			"subject": "Your Re-boarding Request Has Been Approved",
			"response": _signoff_email_shell(SIGNOFF_PASSED_ACCENT, "Approved", "Re-boarding Request Approved", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_REBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or 'Sir/Madam' }},</p>"
			"<p>After review, your re-boarding request with {{ company_name }} (<b>{{ request_name }}</b>) has"
			" not been approved at this time.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_REBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE,
			"subject": "Your Re-boarding Request Has Been Rejected",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Rejected", "Re-boarding Request Rejected", body),
		}).insert(ignore_permissions=True)


def backfill_default_reboarding_request_creator_email_templates():
	# Internal-only, unlike the three vendor-facing ones above — goes to
	# whoever raised the Request (self.owner), not the vendor, same
	# reasoning as Vendor Deboarding Request's own Rejected email
	# (_notify_rejected_unsafe there): the creator has no other way to
	# find out a decision was made unless they keep checking the document
	# by hand. A separate, later patch (not folded into the original
	# backfill_default_reboarding_request_email_templates above) since
	# that one already ran on every existing site by the time these two
	# were added — see this app's own patches.txt convention.
	if not frappe.db.exists("Email Template", DEFAULT_REBOARDING_REQUEST_APPROVED_CREATOR_EMAIL_TEMPLATE):
		body = (
			"<p>Hi,</p>"
			"<p>The Vendor Reboarding Request you raised, <b>{{ request_name }}</b>"
			"{% if vendor_name %} for {{ vendor_name }}{% endif %}, has been <b>Approved</b>.</p>"
			"<p>You can now create Background Check, Compliance Audit, Sampling Evaluation, and Sign-off"
			" documents against it.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_REBOARDING_REQUEST_APPROVED_CREATOR_EMAIL_TEMPLATE,
			"subject": "Vendor Reboarding Request {{ request_name }} Approved",
			"response": _signoff_email_shell(SIGNOFF_PASSED_ACCENT, "Approved", "Re-boarding Request Approved", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_REBOARDING_REQUEST_REJECTED_CREATOR_EMAIL_TEMPLATE):
		body = (
			"<p>Hi,</p>"
			"<p>The Vendor Reboarding Request you raised, <b>{{ request_name }}</b>"
			"{% if vendor_name %} for {{ vendor_name }}{% endif %}, has been <b>Rejected</b>.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_REBOARDING_REQUEST_REJECTED_CREATOR_EMAIL_TEMPLATE,
			"subject": "Vendor Reboarding Request {{ request_name }} Rejected",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Rejected", "Re-boarding Request Rejected", body),
		}).insert(ignore_permissions=True)


# migrate_is_resolvable_check_to_select() used to live here — a one-time
# conversion of is_resolvable from a plain Check (0/1) to today's mandatory
# Select (Yes/No/Maybe), for any row still holding the old literal "0"/"1"
# strings. Removed: the field has been a Select for a while now, and
# nothing currently writing to it can produce those old values again.

DEFAULT_DEBOARDING_CHECKLIST_TEMPLATE = "Default Deboarding Checklist"
DEFAULT_DEBOARDING_CHECKLIST_ITEMS = [
	("Return company-issued assets", "Assets"),
	("Revoke system and portal access", "IT"),
	("Settle pending payments and dues", "Finance"),
	("Collect signed NDA / compliance closure", "Compliance"),
	("Handover documentation and knowledge transfer", "Operations"),
]


def backfill_default_deboarding_checklist_template():
	# default_checklist_template is mandatory on Vendor Lifecycle Settings
	# (a Vendor Deboarding Checklist can't be created at all without one
	# resolving) — a fresh install needs a real, usable template to point
	# at out of the box, not just a mandatory field left blank. Generic/
	# industry-neutral tasks, same as every other master in this app; a
	# deployment is free to edit or replace this template afterward, this
	# only ever fills it in once (never overwrites an existing choice).
	if not frappe.db.exists("Vendor Deboarding Checklist Template", DEFAULT_DEBOARDING_CHECKLIST_TEMPLATE):
		frappe.get_doc({
			"doctype": "Vendor Deboarding Checklist Template",
			"template_name": DEFAULT_DEBOARDING_CHECKLIST_TEMPLATE,
			"items": [{"task": task, "category": category} for task, category in DEFAULT_DEBOARDING_CHECKLIST_ITEMS],
		}).insert(ignore_permissions=True)

	if not frappe.db.get_single_value("Vendor Lifecycle Settings", "default_checklist_template"):
		frappe.db.set_single_value(
			"Vendor Lifecycle Settings", "default_checklist_template", DEFAULT_DEBOARDING_CHECKLIST_TEMPLATE
		)


DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE = "Vendor Deboarding Checklist Task Assigned"
DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE = "Vendor Deboarding Checklist Task Reminder"


def backfill_default_checklist_task_email_templates():
	# Internal, staff-to-staff notifications (the assignee themselves,
	# CC'd Vendor Lifecycle Managers via send_vendor_lifecycle_email's own
	# vendor_lifecycle_cc_list) — referenced directly by name in
	# vendor_deboarding_checklist.py, same "hardcoded, backfilled once"
	# convention as every other Vendor Lifecycle template.
	task_list_html = (
		'<ul style="margin:8px 0;padding-left:20px;">'
		"{% for t in tasks %}"
		"<li>{{ t.task }}{% if t.category %} ({{ t.category }}){% endif %} — {{ t.status }}</li>"
		"{% endfor %}"
		"</ul>"
	)

	if not frappe.db.exists("Email Template", DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>You've been assigned {{ task_count }} task(s) on the deboarding checklist for"
			" <b>{{ firm_name }}</b>:</p>"
			+ task_list_html
			+ '<p><a href="{{ checklist_link }}">View the checklist</a> to update their status.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE,
			"subject": "Deboarding Checklist Task(s) Assigned — {{ firm_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "Checklist Task Assigned", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>A reminder — {{ task_count }} task(s) assigned to you on the deboarding checklist for"
			" <b>{{ firm_name }}</b> are still not complete:</p>"
			+ task_list_html
			+ '<p><a href="{{ checklist_link }}">View the checklist</a> to update their status.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE,
			"subject": "Reminder: Deboarding Checklist Task(s) Still Pending — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Reminder", "Checklist Tasks Pending", body),
		}).insert(ignore_permissions=True)


DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Request"
DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Received"
DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE = "Vendor Deboarding Clearance Certificate Follow-up"


def backfill_default_clearance_certificate_email_templates():
	# Same request / received / follow-up shape as Vendor Sign Off's own
	# Contract-signing flow — Request and Received are vendor-facing;
	# Received is also CC'd to the Vendor Lifecycle Manager automatically
	# (vendor_lifecycle_cc_list); Follow-up reuses the same vendor-facing
	# recipients.
	if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>Please find attached the clearance certificate for <b>{{ firm_name }}</b> as part of the"
			" deboarding process. Kindly sign and reply to this email with the signed copy attached.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE,
			"subject": "Clearance Certificate for Signature — {{ firm_name }}",
			"response": _signoff_email_shell(
				SIGNOFF_REQUEST_ACCENT, "Action Needed", "Clearance Certificate", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>The signed clearance certificate for <b>{{ firm_name }}</b> has been received and attached to"
			' the checklist.</p><p><a href="{{ checklist_link }}">View the checklist</a>.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE,
			"subject": "Signed Clearance Certificate Received — {{ firm_name }}",
			"response": _signoff_email_shell(
				SIGNOFF_RECEIVED_ACCENT, "Received", "Clearance Certificate Received", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>This is a follow-up regarding the clearance certificate sent earlier for <b>{{ firm_name }}</b>"
			" — we haven't yet received the signed copy back. Kindly sign and reply to that email at your"
			" earliest convenience.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE,
			"subject": "Follow-up: Clearance Certificate Still Pending — {{ firm_name }}",
			"response": _signoff_email_shell(
				SIGNOFF_FAILED_ACCENT, "Reminder", "Clearance Certificate Pending", body
			),
		}).insert(ignore_permissions=True)


DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE = "Vendor Sign-off Follow-up"


def backfill_default_signoff_followup_email_template():
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>This is a follow-up regarding the Sign-off document(s) sent earlier for <b>{{ firm_name }}</b>"
			" — we haven't yet received the signed {{ missing_documents }} back. Kindly sign and reply to that"
			" email at your earliest convenience.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE,
			"subject": "Follow-up: Sign-off Document(s) Still Pending — {{ firm_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Reminder", "Sign-off Documents Pending", body),
		}).insert(ignore_permissions=True)


DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE = "Vendor Lifecycle Manual Attachment Needed"


def backfill_default_manual_attach_needed_email_template():
	# Shared by signoff_reply.py and checklist_clearance_reply.py — an
	# inbound reply couldn't be auto-attached (a forward instead of a
	# reply, or more than one attachment), so the Creator and Vendor
	# Lifecycle Manager are told to check and attach it manually.
	if not frappe.db.exists("Email Template", DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>An email reply came in for {{ doctype_label }} <b>{{ document_name }}</b>, but it couldn't be"
			" attached automatically.</p>"
			+ _info_box(VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reason: {{ reason }}")
			+ '<p><a href="{{ document_link }}">Open the document</a> and attach the file manually.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE,
			"subject": "Manual Attachment Needed — {{ doctype_label }} {{ document_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "Manual Attachment Needed", body
			),
		}).insert(ignore_permissions=True)


DEFAULT_ATTACHMENT_SAVE_FAILED_EMAIL_TEMPLATE = "Vendor Lifecycle Attachment Save Failed"


def backfill_default_attachment_save_failed_email_template():
	# Sent by reply_attach_guard.py — an inbound reply was matched and
	# verified, but saving its file onto the document raised an error, so
	# everything was rolled back. Sent on the first failure (a retry is
	# queued) and again if the one retry also fails (attach manually).
	if not frappe.db.exists("Email Template", DEFAULT_ATTACHMENT_SAVE_FAILED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>An email reply came in for {{ doctype_label }} <b>{{ document_name }}</b>, but there was an"
			" error while saving its file onto the document, so nothing was attached.</p>"
			+ _info_box(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT,
				"Document: {{ doctype_label }} {{ document_name }}<br>"
				"{% if error_log_name %}Error Log: <a href=\"{{ error_log_link }}\">{{ error_log_name }}</a>"
				"{% else %}Error Log: the error could not be logged{% endif %}<br>"
				"Reply from: {{ sender }}<br>"
				"Received on: {{ received_on }}",
			)
			+ "{% if will_retry %}"
			"<p>The system will retry once automatically. If the retry also fails you will get another"
			" email, and the file must then be attached manually.</p>"
			"{% else %}"
			'<p><a href="{{ document_link }}">Open the document</a> and attach the file manually from the'
			" vendor's reply in its timeline.</p>"
			"{% endif %}"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_ATTACHMENT_SAVE_FAILED_EMAIL_TEMPLATE,
			"subject": "Attachment Save Failed — {{ doctype_label }} {{ document_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "Attachment Save Failed", body
			),
		}).insert(ignore_permissions=True)


DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DUE_EMAIL_TEMPLATE = "Compliance Audit Renewal Due"
DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE = "Compliance Audit Renewal Draft Reminder"


def backfill_default_compliance_audit_renewal_email_templates():
	# Both internal-only (sent to the Vendor Lifecycle Manager, and the
	# draft's own creator where relevant) - see tasks.
	# send_compliance_audit_renewal_notices / _draft_reminders.
	if not frappe.db.exists("Email Template", DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DUE_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p><b>{{ vendor_name }}</b> ({{ vendor }})'s Compliance Audit is valid until"
			" <b>{{ valid_until }}</b> - {{ days_remaining }} day(s) from now.</p>"
			+ _info_box(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT,
				"Start (or check on) a Renewal Compliance Audit for this vendor before it lapses.",
			)
			+ "<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DUE_EMAIL_TEMPLATE,
			"subject": "Compliance Audit Renewal Due Soon - {{ vendor_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reminder", "Compliance Audit Renewal Due Soon", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>The Renewal Compliance Audit <b>{{ draft_name }}</b> for <b>{{ vendor_name }}</b> ({{ vendor }})"
			" has been sitting as a draft for {{ days_open }} day(s).</p>"
			+ '<p><a href="{{ draft_link }}">Open it</a> and complete it.</p>'
			+ "<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_COMPLIANCE_AUDIT_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE,
			"subject": "Renewal Compliance Audit Still Pending - {{ vendor_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reminder", "Renewal Compliance Audit Still Pending", body
			),
		}).insert(ignore_permissions=True)


DEFAULT_SIGNOFF_RENEWAL_DUE_EMAIL_TEMPLATE = "Sign Off Renewal Due"
DEFAULT_SIGNOFF_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE = "Sign Off Renewal Draft Reminder"


def backfill_default_signoff_renewal_email_templates():
	# Both internal-only (sent to the Vendor Lifecycle Manager, and the
	# draft's own creator where relevant) - see tasks.
	# send_signoff_renewal_notices / _draft_reminders. Same shape as
	# backfill_default_compliance_audit_renewal_email_templates.
	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_RENEWAL_DUE_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p><b>{{ vendor_name }}</b> ({{ vendor }})'s contract is valid until"
			" <b>{{ valid_until }}</b> - {{ days_remaining }} day(s) from now.</p>"
			+ _info_box(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT,
				"Start (or check on) a Renewal Sign Off for this vendor before it lapses.",
			)
			+ "<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_RENEWAL_DUE_EMAIL_TEMPLATE,
			"subject": "Contract Renewal Due Soon - {{ vendor_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reminder", "Contract Renewal Due Soon", body
			),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SIGNOFF_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>The Renewal Sign Off <b>{{ draft_name }}</b> for <b>{{ vendor_name }}</b> ({{ vendor }}) has been"
			" sitting as a draft for {{ days_open }} day(s).</p>"
			+ '<p><a href="{{ draft_link }}">Open it</a> and complete it.</p>'
			+ "<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SIGNOFF_RENEWAL_DRAFT_REMINDER_EMAIL_TEMPLATE,
			"subject": "Renewal Sign Off Still Pending - {{ vendor_name }}",
			"response": _signoff_email_shell(
				VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Reminder", "Renewal Sign Off Still Pending", body
			),
		}).insert(ignore_permissions=True)
