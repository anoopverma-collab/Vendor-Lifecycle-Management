# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import glob
import os
import shutil

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

	# Web Forms are "is_standard" (JSON-file-owned, like a DocType or Page),
	# but migrate's automatic module sync doesn't cover them the way it does
	# DocType/Page/Report/Workspace — an edit to a Web Form's JSON otherwise
	# just sits on disk forever without ever reaching the database. Force-
	# import every one this app ships, every migrate, same as above.
	web_form_dir = frappe.get_app_path("vendor_lifecycle", "vendor_lifecycle", "web_form")
	for path in glob.glob(os.path.join(web_form_dir, "*", "*.json")):
		import_file_by_path(path, force=True)


def before_migrate():
	rename_license_template_doctype()
	rename_insurance_template_doctype()
	# Must run before sync_dashboards() (which happens early in migrate,
	# well before after_migrate hooks) - otherwise this leftover empty
	# folder from the Number Card rename still triggers the "missing"
	# warning on this exact run, one time, before after_migrate() ever
	# gets a chance to clean it up.
	remove_stale_module_directory("number_card", "vendors_through_app")


def rename_license_template_doctype():
	# "Audit License Template" (+ its child "Audit License Template Item")
	# was renamed to "Licenses and Permits Template" for consistency with
	# the "Licenses & Permits" table it feeds on Vendor Compliance Audit.
	# Must run here, in before_migrate — by the time after_migrate hooks
	# run, sync_all() has already synced the new doctype JSON files AND
	# removed the old DocType's now-sourceless meta record as an "orphan"
	# (harmless on its own — the doctype's table survives untouched — but
	# without frappe.rename_doc, any existing License Template records
	# would be stranded in that orphaned table, inaccessible through the
	# new doctype). Renaming here, before sync_all ever runs, uses the
	# real DB RENAME TABLE path instead, carrying any existing records
	# (and every Link field pointing at them) over cleanly. Same pattern
	# Frappe core itself uses for DocType renames (e.g.
	# frappe/patches/v15_0/migrate_to_utm.py).
	if not frappe.db.exists("DocType", "Audit License Template") and not frappe.db.exists(
		"DocType", "Audit License Template Item"
	):
		return

	# DocType's own after_rename tries to move the old doctype's folder/files
	# on disk to the new location — but the source already ships the new
	# files (and the old ones are already gone), so that move would fail.
	# frappe.flags.in_patch skips that step; it's what real patches rely on
	# too, just set for them automatically by patch_handler.run_all(), which
	# doesn't apply to a before_migrate hook.
	#
	# Each rename is guarded independently (not just once, up front) so a
	# migrate that died partway through a previous attempt — one renamed,
	# the other not — can simply be re-run rather than needing manual cleanup.
	frappe.flags.in_patch = True
	try:
		if frappe.db.exists("DocType", "Audit License Template Item"):
			frappe.rename_doc("DocType", "Audit License Template Item", "Licenses and Permits Template Item", force=True)
		if frappe.db.exists("DocType", "Audit License Template"):
			frappe.rename_doc("DocType", "Audit License Template", "Licenses and Permits Template", force=True)
	finally:
		frappe.flags.in_patch = False
	frappe.reload_doctype("Licenses and Permits Template Item", force=True)
	frappe.reload_doctype("Licenses and Permits Template", force=True)


def rename_insurance_template_doctype():
	# Same rename, same reasoning as rename_license_template_doctype() above —
	# "Audit Insurance Template" (+ its child "Audit Insurance Template Item")
	# renamed to "Insurance Template", matching the "Insurance Template"
	# label the field on Vendor Compliance Audit already used.
	if not frappe.db.exists("DocType", "Audit Insurance Template") and not frappe.db.exists(
		"DocType", "Audit Insurance Template Item"
	):
		return

	frappe.flags.in_patch = True
	try:
		if frappe.db.exists("DocType", "Audit Insurance Template Item"):
			frappe.rename_doc("DocType", "Audit Insurance Template Item", "Insurance Template Item", force=True)
		if frappe.db.exists("DocType", "Audit Insurance Template"):
			frappe.rename_doc("DocType", "Audit Insurance Template", "Insurance Template", force=True)
	finally:
		frappe.flags.in_patch = False
	frappe.reload_doctype("Insurance Template Item", force=True)
	frappe.reload_doctype("Insurance Template", force=True)


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
	# frappe.installer.install_app() only fires this after_install hook,
	# then syncs this app's fixtures/*.json (Role, Custom Field, Property
	# Setter) *afterward* - so on a genuinely fresh install, the "Vendor
	# Lifecycle Manager"/"Vendor Lifecycle User" roles the new Vendor KYC
	# Workflow links to wouldn't exist yet at this point unless fixtures
	# are synced here first, ahead of after_migrate()'s own seed functions.
	from frappe.utils.fixtures import sync_fixtures

	sync_fixtures("vendor_lifecycle")
	# after_migrate() already starts with sync_standard_files() itself, so
	# nothing else is needed here.
	after_migrate()


# Any string that only appears in the current _signoff_email_shell() output —
# used to tell an already-restyled template apart from one still carrying the
# older, plainer shell.
EMAIL_TEMPLATE_STYLE_MARKER = "box-shadow:0 2px 10px"


def resync_default_email_template_styling():
	# The backfill_default_*_email_template*() calls below are all
	# "create if missing" — once a template exists in the DB, editing its
	# HTML here has no effect on migrate. Delete any default template still
	# carrying the old, unstyled shell so the very next backfill call in
	# after_migrate() recreates it fresh with the current styling. Once a
	# template's response carries the marker, this is a no-op for it forever.
	names = [
		DEFAULT_SIGNOFF_EMAIL_TEMPLATE,
		DEFAULT_SIGNOFF_RECEIVED_EMAIL_TEMPLATE,
		DEFAULT_SIGNOFF_PASSED_EMAIL_TEMPLATE,
		DEFAULT_SIGNOFF_FAILED_EMAIL_TEMPLATE,
		DEFAULT_VENDOR_LIFECYCLE_PASSED_EMAIL_TEMPLATE,
		DEFAULT_VENDOR_LIFECYCLE_FAILED_EMAIL_TEMPLATE,
		DEFAULT_ONBOARDING_RECEIVED_EMAIL_TEMPLATE,
		DEFAULT_ONBOARDING_NEW_REQUEST_EMAIL_TEMPLATE,
		DEFAULT_SATISFACTION_SURVEY_CREATED_EMAIL_TEMPLATE,
		DEFAULT_SATISFACTION_SURVEY_REMINDER_EMAIL_TEMPLATE,
		DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE,
		DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE,
		DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE,
		DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE,
		DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE,
		DEFAULT_DEBOARDING_REQUEST_CREATED_EMAIL_TEMPLATE,
		DEFAULT_DEBOARDING_REQUEST_REJECTED_EMAIL_TEMPLATE,
		DEFAULT_DEBOARDING_REQUEST_APPROVED_EMAIL_TEMPLATE,
		DEFAULT_CHECKLIST_TASK_ASSIGNED_EMAIL_TEMPLATE,
		DEFAULT_CHECKLIST_TASK_REMINDER_EMAIL_TEMPLATE,
		DEFAULT_CLEARANCE_CERTIFICATE_EMAIL_TEMPLATE,
		DEFAULT_CLEARANCE_CERTIFICATE_RECEIVED_EMAIL_TEMPLATE,
		DEFAULT_CLEARANCE_CERTIFICATE_FOLLOWUP_EMAIL_TEMPLATE,
		DEFAULT_SIGNOFF_FOLLOWUP_EMAIL_TEMPLATE,
		DEFAULT_MANUAL_ATTACH_NEEDED_EMAIL_TEMPLATE,
	]
	for name in names:
		response = frappe.db.get_value("Email Template", name, "response")
		if response and EMAIL_TEMPLATE_STYLE_MARKER not in response:
			frappe.db.delete("Email Template", {"name": name})


def after_migrate():
	sync_standard_files()
	resync_default_email_template_styling()
	migrate_reference_check_mandatory_setting()
	backfill_settings_defaults()
	backfill_default_satisfaction_rating_template()
	backfill_last_satisfaction_survey_date()
	rename_ndc_terminology()
	migrate_onboarding_request_to_submittable()
	remove_stale_setting("request_edit_override_role")
	migrate_kyc_firm_address_to_address_line_1()
	normalize_kyc_state_casing()
	migrate_kyc_status_draft_to_in_progress()
	backfill_kyc_status_from_docstatus()
	install_vendor_kyc_workflow()
	migrate_supplier_hold_to_is_frozen()
	remove_stale_client_script("Vendor Background Check Load Rating Template Button")
	migrate_compliance_checks_to_child_table()
	for name in REMOVED_CLIENT_SCRIPTS:
		remove_stale_client_script(name)
	backfill_missing_compliance_check_template()
	backfill_result_method_resolved()
	_ensure_default_checklist_template()
	rename_estimated_monthly_capacity()
	migrate_facility_applicable_to_yes_no()
	backfill_license_insurance_masters()
	seed_indian_states()
	migrate_signoff_email_settings_to_vendor_lifecycle()
	backfill_default_signoff_email_template()
	backfill_default_signoff_received_email_template()
	backfill_default_signoff_passed_email_template()
	backfill_default_signoff_failed_email_template()
	backfill_default_vendor_lifecycle_stage_email_templates()
	backfill_default_satisfaction_survey_email_templates()
	backfill_default_support_ticket_email_templates()
	remove_stale_setting("esign_provider")
	remove_stale_setting("supplier_unfreeze_role")
	remove_stale_setting("signoff_email_account")
	remove_stale_setting("signoff_email_always_cc")
	remove_stale_setting("signoff_email_template")
	remove_stale_setting("signoff_received_email_template")
	remove_stale_setting("signoff_passed_email_template")
	remove_stale_setting("signoff_failed_email_template")
	remove_stale_web_form("vendor-signoff-upload")
	remove_stale_setting("enforce_sequential_stages")
	remove_stale_setting("sampling_mandatory")
	# Both of these must run before backfill_sampling_mandatory_business_
	# types() - it does a full settings.save(), which used to validate every
	# mandatory field on this Settings singleton, including the two these
	# backfill. ignore_mandatory=True on that save() is a second safety net
	# now, but keeping the real dependency order here is still the correct
	# fix, not just a workaround.
	backfill_default_deboarding_rating_template()
	backfill_default_deboarding_checklist_template()
	backfill_sampling_mandatory_business_types()
	migrate_background_check_result_method_off_average()
	remove_stale_setting("minimum_average_rating")
	remove_stale_web_form("vendor-satisfaction-survey")
	remove_stale_setting("disable_timing")
	backfill_default_deboarding_request_email_templates()
	migrate_is_resolvable_check_to_select()
	backfill_default_checklist_task_email_templates()
	backfill_default_clearance_certificate_email_templates()
	backfill_default_signoff_followup_email_template()
	remove_stale_setting("deboarding_notification_provider")
	backfill_default_manual_attach_needed_email_template()
	remove_stale_number_card("Vendors Through App")
	install_getting_started_sample()


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


def migrate_background_check_result_method_off_average():
	# "Average" was removed from Background Check Result Method's own
	# options — averaging each Reference's own average_rating silently
	# dropped any Reference scored via a non-Average Reference Result
	# Method (its own average_rating stays blank even though it has a real
	# Passed/Failed outcome), so a genuinely Failed reference could vanish
	# from the aggregate instead of failing the Background Check. A site
	# already on "Average" is moved to the new default so it doesn't keep
	# a now-invalid Select value sitting in the DB.
	if frappe.db.get_single_value("Vendor Lifecycle Settings", "background_check_result_method") == "Average":
		frappe.db.set_single_value(
			"Vendor Lifecycle Settings", "background_check_result_method", "Each Reference Must Pass"
		)


def remove_stale_setting(fieldname):
	# "Vendor Lifecycle Settings" is a Single — removing a field from its
	# JSON leaves an orphan row sitting in the shared `tabSingles` table
	# forever unless explicitly cleaned up (there's no column to drop).
	frappe.db.delete("Singles", {"doctype": "Vendor Lifecycle Settings", "field": fieldname})


def migrate_signoff_email_settings_to_vendor_lifecycle():
	# signoff_email_account/signoff_email_always_cc were generalized into
	# vendor_lifecycle_email_account/vendor_lifecycle_email_always_cc — now
	# shared by every Vendor Lifecycle email, not just Sign-off's. Carries
	# over whatever a deployment already had configured (renaming a field
	# in JSON doesn't move its value in the shared `tabSingles` table), and
	# turns "Use Emails" on for a site that already had a working account
	# configured, so already-flowing email doesn't go silently quiet.
	#
	# Reads straight from Singles (not get_single_value) — by the time
	# after_migrate hooks run, the DocType's own JSON has already been
	# synced and no longer declares the old fieldname, so get_single_value
	# would refuse it as an unknown field even though the old row is still
	# sitting in the table.
	def _old_singles_value(fieldname):
		# frappe.db.get_value("Singles", ...) treats it like a normal
		# DocType and adds an "order by creation" clause — the raw
		# tabSingles table has no such column, so that raises. Plain SQL
		# sidesteps it.
		rows = frappe.db.sql(
			"select `value` from `tabSingles` where doctype=%s and field=%s limit 1",
			("Vendor Lifecycle Settings", fieldname),
		)
		return rows[0][0] if rows else None

	old_account = _old_singles_value("signoff_email_account")
	if old_account and not frappe.db.get_single_value("Vendor Lifecycle Settings", "vendor_lifecycle_email_account"):
		frappe.db.set_single_value("Vendor Lifecycle Settings", "vendor_lifecycle_email_account", old_account)
		frappe.db.set_single_value("Vendor Lifecycle Settings", "enable_vendor_lifecycle_emails", 1)

	old_cc = _old_singles_value("signoff_email_always_cc")
	if old_cc and not frappe.db.get_single_value("Vendor Lifecycle Settings", "vendor_lifecycle_email_always_cc"):
		frappe.db.set_single_value("Vendor Lifecycle Settings", "vendor_lifecycle_email_always_cc", old_cc)


def remove_stale_client_script(name):
	# Fixture sync only inserts/updates records still present in the
	# fixture file — a Client Script removed from fixtures/client_script.json
	# (because the field/method it depended on no longer exists) is left
	# behind in the database forever unless deleted explicitly here.
	frappe.db.delete("Client Script", {"name": name})


def remove_stale_web_form(name):
	# Migrate's own "Removing orphan doctypes" step only targets DocType
	# records — a Web Form whose JSON file has been deleted is left behind
	# in the database forever otherwise, same as a Client Script above.
	frappe.db.delete("Web Form", {"name": name})


def remove_stale_number_card(name):
	# Same story as remove_stale_web_form above - migrate's "Removing
	# orphan doctypes" step doesn't cover Number Card either, so a renamed
	# one (its old JSON file deleted, a new one added under the new name)
	# leaves the old DB record behind forever, and every subsequent migrate
	# logs a harmless but noisy "<old path>.json missing" warning trying to
	# resync it.
	frappe.db.delete("Number Card", {"name": name})


def remove_stale_module_directory(subfolder, name):
	# Deleting every file inside a module subfolder (e.g. renaming a Number
	# Card) doesn't remove the now-empty folder itself from anyone's
	# existing checkout - git only tracks files, not directories, so
	# `git pull` leaves it sitting there empty. Frappe's own dashboard sync
	# then lists every folder under number_card/ and tries to load a .json
	# out of each one it finds, logging a harmless but noisy "missing"
	# warning for this one every migrate. Removing it here means nobody who
	# already had the old file needs to clean it up by hand.
	path = frappe.get_app_path("vendor_lifecycle", "vendor_lifecycle", subfolder, name)
	if os.path.isdir(path):
		shutil.rmtree(path)


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
def install_getting_started_sample():
	_install_sample_form_tours()
	_install_sample_module_onboarding()


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
					"position": "Left",
				},
				{
					"title": "Firm Name",
					"fieldname": "firm_name",
					"fieldtype": "Data",
					"label": "Firm Name",
					"description": "Carried over from the Onboarding Request - double-check it's correct. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Legal Entity Type",
					"fieldname": "legal_entity_type",
					"fieldtype": "Select",
					"label": "Legal Entity Type",
					"description": "How this vendor is legally structured - Sole Proprietorship, Private Limited, Partnership, and so on. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Billing Currency",
					"fieldname": "billing_currency",
					"fieldtype": "Link",
					"label": "Billing Currency",
					"description": "The Supplier Setup section - which currency this vendor is billed and paid in.",
					"position": "Left",
				},
				{
					"title": "Contact Person Name",
					"fieldname": "contact_person_name",
					"fieldtype": "Data",
					"label": "Contact Person Name",
					"description": "The Contact tab - who to reach at this vendor for day-to-day communication. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Contact Person Number",
					"fieldname": "contact_person_number",
					"fieldtype": "Phone",
					"label": "Contact Person Number",
					"description": "A direct phone number for the Contact Person above. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Official Email",
					"fieldname": "official_email",
					"fieldtype": "Data",
					"label": "Official Email",
					"description": "Where every KYC-related email to this vendor goes. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Address Line 1",
					"fieldname": "address_line_1",
					"fieldtype": "Data",
					"label": "Address Line 1",
					"description": "The Address tab - the first line of this vendor's full postal address, used for the GSTIN checks further on for India-based vendors. Mandatory.",
					"position": "Left",
				},
				{
					"title": "City",
					"fieldname": "city",
					"fieldtype": "Data",
					"label": "City",
					"description": "The city this vendor's registered address is in. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Country",
					"fieldname": "country",
					"fieldtype": "Link",
					"label": "Country",
					"description": "Mandatory - also decides whether the India-specific PAN/GSTIN fields on the Tax & Compliance tab show up.",
					"position": "Left",
				},
				{
					"title": "Postal / ZIP Code",
					"fieldname": "pincode",
					"fieldtype": "Data",
					"label": "Postal / ZIP Code",
					"description": "The postal or ZIP code for the address above. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Business Type",
					"fieldname": "business_type",
					"fieldtype": "Select",
					"label": "Business Type",
					"description": "The Business Details tab - once you pick a Business Type, extra fields specific to that type of vendor appear below it. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Tax & Compliance",
					"fieldname": "tax_id",
					"fieldtype": "Data",
					"label": "Tax ID (PAN / VAT / EIN, etc.)",
					"description": "PAN, GSTIN, business registration, and compliance certificates all live on this tab.",
					"position": "Left",
				},
				{
					"title": "Bank Details",
					"fieldname": "bank_account_name",
					"fieldtype": "Data",
					"label": "Bank Account Name",
					"description": "Needed before any payment can be made to this vendor.",
					"position": "Left",
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
					"position": "Left",
				},
				{
					"title": "Business Type",
					"fieldname": "business_type",
					"fieldtype": "Select",
					"label": "Business Type",
					"description": "What the vendor does - once you pick one, an \"About Your Business\" section appears further down with extra fields specific to that type of vendor.",
					"position": "Left",
				},
				{
					"title": "Contact & Address",
					"fieldname": "contact_person",
					"fieldtype": "Data",
					"label": "Contact Person",
					"description": "This section covers the vendor's basic reachability - Contact Person, Phone, Email, and full Address.",
					"position": "Left",
				},
				{
					"title": "Profile & Catalogue",
					"fieldname": "profile_attachment",
					"fieldtype": "Attach",
					"label": "Business / Capability Profile",
					"description": "Optional supporting documents - a company profile, product catalogue, or anything else worth attaching.",
					"position": "Left",
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
					"position": "Left",
				},
				{
					"title": "Reason",
					"fieldname": "reason",
					"fieldtype": "Small Text",
					"label": "Reason",
					"description": "Why this vendor is being deboarded. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Is the Issue Resolvable?",
					"fieldname": "is_resolvable",
					"fieldtype": "Select",
					"label": "Is the Issue with the Vendor Resolvable?",
					"description": "A judgment call on whether this could still be fixed rather than ending the relationship. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Ratings",
					"fieldname": "ratings",
					"fieldtype": "Table",
					"label": "Ratings",
					"description": "Score every row here before this request can be saved - a final performance record for this vendor.",
					"position": "Left",
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
					"position": "Left",
				},
				{
					"title": "Company",
					"fieldname": "company",
					"fieldtype": "Link",
					"label": "Company",
					"description": "Which Company this Checklist belongs to - used to resolve the GSTIN shown on the clearance certificate email.",
					"position": "Left",
				},
				{
					"title": "Additional Email",
					"fieldname": "additional_email",
					"fieldtype": "Data",
					"label": "Additional Supplier Email",
					"description": "An extra recipient for the clearance certificate email, alongside the vendor's own KYC/Supplier contacts.",
					"position": "Left",
				},
				{
					"title": "Checklist Items",
					"fieldname": "checklist_items",
					"fieldtype": "Table",
					"label": "Checklist Items",
					"description": "Every task needs a status (Completed/Invalid/Unable to Complete) and at least one assignee before this Checklist can be submitted.",
					"position": "Left",
				},
				{
					"title": "Clearance",
					"fieldname": "no_clearance_certificate",
					"fieldtype": "Check",
					"label": "No Clearance Certificate Available",
					"description": "Attach the signed clearance certificate below, or tick this and give a reason if one genuinely isn't available.",
					"position": "Left",
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
					"position": "Left",
				},
				{
					"title": "Period",
					"fieldname": "period",
					"fieldtype": "Select",
					"label": "Period",
					"description": "Which period this survey covers. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Survey Date",
					"fieldname": "survey_date",
					"fieldtype": "Date",
					"label": "Survey Date",
					"description": "When this survey was conducted. Mandatory.",
					"position": "Left",
				},
				{
					"title": "Ratings",
					"fieldname": "ratings",
					"fieldtype": "Table",
					"label": "Ratings",
					"description": "Score every criteria row here - the actual satisfaction scoring for this vendor.",
					"position": "Left",
				},
				{
					"title": "Feedback",
					"fieldname": "issues",
					"fieldtype": "Small Text",
					"label": "Areas of Concern",
					"description": "The Feedback section - Areas of Concern and Suggestions for Improvement, both optional free text.",
					"position": "Left",
				},
			],
		}).insert(ignore_permissions=True)


def _install_sample_module_onboarding():
	if not frappe.db.exists("Onboarding Step", "Vendor Lifecycle Settings Setup"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor Lifecycle Settings Setup",
			"title": "Set Up Vendor Lifecycle Settings",
			# Deliberately plain navigation, not a Form Tour - Vendor
			# Lifecycle Settings is a Single doctype, and Frappe's own tour
			# engine has a real compatibility gap with Singles (the tour
			# cancels itself before ever showing anything, traced to an
			# extra internal navigation step Singles trigger on load that
			# a regular doctype's form doesn't). Not something worth
			# fighting for a light sample - just get the user there.
			"action": "Update Settings",
			"reference_document": "Vendor Lifecycle Settings",
			"validate_action": 0,
			"action_label": "Review Settings",
			"description": "Review the default templates, mandatory checks, and email account before anything else.",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Onboarding Step", "Vendor Onboarding Request Tour Step"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor Onboarding Request Tour Step",
			"title": "Take the Onboarding Tour",
			# The floating "Getting Started" panel (OnboardingPanel.vue)
			# shows action_label as each step's own button text - unlike
			# the older block-widget renderer (onboarding_widget.js), it has
			# no fallback to title/action if this is left blank, so it just
			# renders empty.
			"action_label": "Onboarding Request",
			"action": "Show Form Tour",
			"reference_document": "Vendor Onboarding Request",
			"form_tour": "Vendor Onboarding Request Tour",
			"description": "A quick walkthrough of the first form in the onboarding pipeline.",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Onboarding Step", "Vendor KYC Tour Step"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor KYC Tour Step",
			"title": "Take the KYC Tour",
			"action_label": "Vendor KYC",
			"action": "Show Form Tour",
			"reference_document": "Vendor KYC",
			"form_tour": "Vendor KYC Tour",
			"description": "A quick walkthrough of the KYC form - verification, firm details, address, business type, tax, and bank details.",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Onboarding Step", "Vendor Deboarding Request Tour Step"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor Deboarding Request Tour Step",
			"title": "Take the Deboarding Request Tour",
			"action_label": "Deboarding Request",
			"action": "Show Form Tour",
			"reference_document": "Vendor Deboarding Request",
			"form_tour": "Vendor Deboarding Request Tour",
			"description": "A quick walkthrough of the Deboarding Request form.",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Onboarding Step", "Vendor Deboarding Checklist Tour Step"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor Deboarding Checklist Tour Step",
			"title": "Take the Deboarding Checklist Tour",
			"action_label": "Deboarding Checklist",
			"action": "Show Form Tour",
			"reference_document": "Vendor Deboarding Checklist",
			"form_tour": "Vendor Deboarding Checklist Tour",
			"description": "A quick walkthrough of the Deboarding Checklist form.",
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Onboarding Step", "Vendor Satisfaction Survey Tour Step"):
		frappe.get_doc({
			"doctype": "Onboarding Step",
			"name": "Vendor Satisfaction Survey Tour Step",
			"title": "Take the Satisfaction Survey Tour",
			"action_label": "Satisfaction Survey",
			"action": "Show Form Tour",
			"reference_document": "Vendor Satisfaction Survey",
			"form_tour": "Vendor Satisfaction Survey Tour",
			"description": "A quick walkthrough of the Satisfaction Survey form.",
		}).insert(ignore_permissions=True)

	if frappe.db.exists("Module Onboarding", "Vendor Lifecycle Onboarding"):
		return
	frappe.get_doc({
		"doctype": "Module Onboarding",
		"name": "Vendor Lifecycle Onboarding",
		"title": "Vendor Lifecycle Setup",
		"module": "Vendor Lifecycle",
		"allow_roles": [{"role": "Vendor Lifecycle Manager"}],
		"steps": [
			{"step": "Vendor Onboarding Request Tour Step"},
			{"step": "Vendor KYC Tour Step"},
			{"step": "Vendor Deboarding Request Tour Step"},
			{"step": "Vendor Deboarding Checklist Tour Step"},
			{"step": "Vendor Satisfaction Survey Tour Step"},
			{"step": "Vendor Lifecycle Settings Setup"},
		],
	}).insert(ignore_permissions=True)


# All Client Script logic in this app was moved into real, committed .js
# files (doctype .js/_list.js files, and Supplier's via doctype_js/
# doctype_list_js in hooks.py) — Client Script is editable/disable-able by
# any System Manager straight from the browser, with no code review and no
# git history, which this app no longer wants for anything it owns. The
# fixtures/client_script.json file itself was deleted, so fixture sync will
# never recreate these — but a site that already had them (from before this
# change) needs them removed explicitly here, the same as any other
# already-removed fixture (see remove_stale_client_script above).
REMOVED_CLIENT_SCRIPTS = [
	"Supplier Hide Direct Create Button",
	"Supplier Hide Vendor Lifecycle Connections Add Button",
	"Vendor Background Check Hide Direct Create Button",
	"Vendor Background Check Reference Hide Direct Create Button",
	"Vendor KYC Create Additional Records Button",
	"Vendor KYC Direct Create Notice",
	"Vendor KYC Freeze Supplier Button",
	"Vendor KYC GSTIN Address Buttons",
	"Vendor KYC Hide Connections Add Button",
	"Vendor KYC Hide Direct Create Button",
	"Vendor KYC PAN Auto-Extract from GSTIN",
	"Vendor KYC Reject Button",
	"Vendor Onboarding Request Hide Connections Add Button",
	"Vendor Onboarding Request Start KYC Button",
	"Vendor Sampling Evaluation Load Template Button",
]


def migrate_reference_check_mandatory_setting():
	# "Vendor Reference Check" was renamed to "Vendor Background Check" and
	# merged with what used to be Compliance Audit's background-check
	# fields — carry over whatever an admin had already set for the old
	# "reference_check_mandatory" toggle before its column gets dropped,
	# rather than silently resetting to the new field's own default.
	# "Vendor Lifecycle Settings" is a Single — its field values live in the
	# shared `tabSingles` table, not a dedicated table, so column existence
	# has to be checked there rather than with has_column().
	if frappe.db.exists(
		"Singles", {"doctype": "Vendor Lifecycle Settings", "field": "reference_check_mandatory"}
	) and not frappe.db.exists(
		"Singles", {"doctype": "Vendor Lifecycle Settings", "field": "background_check_mandatory"}
	):
		old_value = frappe.db.get_single_value("Vendor Lifecycle Settings", "reference_check_mandatory")
		if old_value is not None:
			frappe.db.set_single_value("Vendor Lifecycle Settings", "background_check_mandatory", old_value)


# "NDC" (No Dues Certificate) was renamed to the region-neutral "Clearance
# Certificate" — a site that already had a value stored under the old option
# names would otherwise be left with a value that no longer matches either
# of the current Select options.
NDC_TO_CLEARANCE = {
	"After NDC": "After Clearance",
	"Before NDC": "Before Clearance",
}


def rename_ndc_terminology():
	# disable_timing itself was later removed entirely (deboarding now only
	# disables a vendor once its Checklist is submitted) — this one-time
	# terminology rename has nothing left to do once that field is gone.
	# Vendor Lifecycle Settings is a Single, so its values live in the
	# shared `tabSingles` table (has_column doesn't apply — there's no
	# dedicated table to check columns on).
	if not frappe.get_meta("Vendor Lifecycle Settings").has_field("disable_timing"):
		return
	current = frappe.db.get_single_value("Vendor Lifecycle Settings", "disable_timing")
	if current in NDC_TO_CLEARANCE:
		frappe.db.set_single_value("Vendor Lifecycle Settings", "disable_timing", NDC_TO_CLEARANCE[current])


def migrate_onboarding_request_to_submittable():
	# Vendor Onboarding Request dropped its custom status field (Draft/
	# Accepted/Rejected) in favor of Frappe's native submit/cancel lifecycle
	# — submitting a request now *is* the "Accepted" decision, and Frappe's
	# own docstatus protection replaces the old manual edit-lock. A request
	# that was already "Accepted", or that already has a Vendor KYC started
	# against it (which could only happen if it had been accepted at some
	# point, whatever its status was later changed to), gets docstatus=1 so
	# real, already-processed work doesn't get silently reset to Draft.
	#
	# The old `status` column itself has to be dropped separately, outside
	# of this hook — an ALTER TABLE here triggers an implicit commit
	# mid-migrate, which Frappe blocks (ImplicitCommitError). It's a harmless
	# orphan column until then; nothing in this app reads it anymore.
	if not frappe.db.has_column("Vendor Onboarding Request", "status"):
		return

	processed = frappe.db.sql(
		"""
		select distinct vor.name
		from `tabVendor Onboarding Request` vor
		left join `tabVendor KYC` kyc on kyc.onboarding_request = vor.name
		where vor.status = 'Accepted' or kyc.name is not null
		""",
		as_dict=True,
	)
	for row in processed:
		frappe.db.set_value("Vendor Onboarding Request", row.name, "docstatus", 1)


def migrate_kyc_firm_address_to_address_line_1():
	# Vendor KYC's single "Firm Address" field was split into "Address Line 1"
	# / "Address Line 2" to match how Vendor Onboarding Request already
	# captures address lines — carry over whatever was already typed in
	# rather than silently losing it. Everything goes into line 1 as-is;
	# there's no reliable way to split free text back into two lines.
	#
	# The old `firm_address` column itself has to be dropped separately,
	# outside of this hook — same ImplicitCommitError reason as the
	# Onboarding Request `status` column above.
	if not frappe.db.has_column("Vendor KYC", "firm_address"):
		return

	rows = frappe.db.sql(
		"""
		select name, firm_address from `tabVendor KYC`
		where firm_address is not null and firm_address != ''
		""",
		as_dict=True,
	)
	for row in rows:
		frappe.db.set_value("Vendor KYC", row.name, "address_line_1", row.firm_address)


def normalize_kyc_state_casing():
	# India Compliance's Address controller rejects any state name that
	# isn't an exact case match against its official list — existing Vendor
	# KYC records saved before that constraint was enforced here (e.g.
	# "rajasthan" instead of "Rajasthan") would otherwise fail the very next
	# time they're saved or submitted. One-time, case-insensitive cleanup.
	if "india_compliance" not in frappe.get_installed_apps():
		return

	from india_compliance.gst_india.constants import STATE_NUMBERS

	rows = frappe.db.sql(
		"""
		select name, state from `tabVendor KYC`
		where country = 'India' and state is not null and state != ''
		""",
		as_dict=True,
	)
	official_by_lower = {s.lower(): s for s in STATE_NUMBERS}
	for row in rows:
		match = official_by_lower.get(row.state.strip().lower())
		if match and match != row.state:
			frappe.db.set_value("Vendor KYC", row.name, "state", match)


def migrate_kyc_status_draft_to_in_progress():
	# "Draft" was renamed to "In Progress" (Select options: In Progress /
	# Verified / Rejected, later Approved instead of Verified) when the
	# Reject feature was added — existing records still hold the old
	# label. A submitted (docstatus 1) record that never got flipped to
	# the submitted label is a separate, pre-existing data gap (should
	# have happened in on_submit) fixed here at the same time, rather
	# than left stuck on a status that no longer exists at all.
	frappe.db.sql("""
		update `tabVendor KYC` set status = 'In Progress'
		where status = 'Draft' and docstatus = 0
	""")
	frappe.db.sql("""
		update `tabVendor KYC` set status = 'Approved'
		where status = 'Draft' and docstatus = 1
	""")


def backfill_kyc_status_from_docstatus():
	# migrate_kyc_status_draft_to_in_progress() above only ever caught
	# records still carrying the old "Draft" label — the actual root
	# cause (on_submit()/on_cancel() setting self.status via a bare
	# assignment, which happens after Frappe has already written the
	# document to the database for that request and so never actually
	# persists) was never fixed at the code level until now, so any KYC
	# submitted or cancelled *after* that migration ran kept getting
	# stuck showing whatever the current default ("In Progress") already
	# was — indistinguishable from "never changed" — rather than "Draft".
	# Catches every remaining straggler directly from docstatus instead
	# of matching a specific stale label. "Verified" was later renamed to
	# "Approved", and "Cancelled" became its own real status instead of a
	# bounce back to "In Progress" once the Vendor KYC Workflow was added
	# — this backfill's targets follow both renames so it still writes a
	# currently-valid option if it ever needs to run again.
	frappe.db.sql("""
		update `tabVendor KYC` set status = 'Approved'
		where docstatus = 1 and status != 'Approved'
	""")
	frappe.db.sql("""
		update `tabVendor KYC` set status = 'Cancelled'
		where docstatus = 2 and status != 'Cancelled'
	""")


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
}
# (state, action, next_state, allowed role) - one role per transition, per
# the actual review split: only a User can send for approval, only a
# Manager can decide from there.
VENDOR_KYC_WORKFLOW_TRANSITIONS = [
	("In Progress", "Send for Approval", "Approval Pending", "Vendor Lifecycle User"),
	("Approval Pending", "Approve", "Approved", "Vendor Lifecycle Manager"),
	("Approval Pending", "Reject", "Rejected", "Vendor Lifecycle Manager"),
	("Approved", "Cancel", "Cancelled", "Vendor Lifecycle Manager"),
]


def install_vendor_kyc_workflow():
	for state, (_doc_status, _allow_edit, style) in VENDOR_KYC_WORKFLOW_STATES.items():
		if frappe.db.exists("Workflow State", state):
			continue
		frappe.get_doc({
			"doctype": "Workflow State",
			"workflow_state_name": state,
			"style": style,
		}).insert(ignore_permissions=True)

	# A fresh site only ships 3 default Workflow Action Master records
	# (Approve/Reject/Review, seeded by frappe/utils/install.py) - any other
	# action name, like our own "Send for Approval"/"Cancel", normally only
	# gets created as a side effect of typing it into the Workflow Builder
	# UI. Since this Workflow is created here in code, never through that
	# UI, create whichever ones are missing ourselves first - otherwise the
	# Workflow Transition rows below fail link validation on a truly fresh
	# install.
	actions = {action for _state, action, _next_state, _allowed in VENDOR_KYC_WORKFLOW_TRANSITIONS}
	for action in actions:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({
				"doctype": "Workflow Action Master",
				"workflow_action_name": action,
			}).insert(ignore_permissions=True)

	if frappe.db.exists("Workflow", "Vendor KYC"):
		workflow = frappe.get_doc("Workflow", "Vendor KYC")
	else:
		workflow = frappe.new_doc("Workflow")
		workflow.workflow_name = "Vendor KYC"
		workflow.document_type = "Vendor KYC"
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
	# the one workflow the app installs, so there's nothing a site admin
	# could have customized on it yet to preserve; simplest way to keep it
	# in sync as the spec itself changes (e.g. adding Approval Pending here).
	current_states = [
		(s.state, s.doc_status, s.allow_edit) for s in workflow.states
	]
	desired_states = [
		(state, doc_status, allow_edit)
		for state, (doc_status, allow_edit, _style) in VENDOR_KYC_WORKFLOW_STATES.items()
	]
	current_transitions = [
		(t.state, t.action, t.next_state, t.allowed) for t in workflow.transitions
	]
	if (
		current_states == desired_states
		and current_transitions == VENDOR_KYC_WORKFLOW_TRANSITIONS
		and not state_field_changed
	):
		return

	workflow.set("states", [])
	workflow.set("transitions", [])
	for state, (doc_status, allow_edit, _style) in VENDOR_KYC_WORKFLOW_STATES.items():
		workflow.append("states", {
			"state": state,
			"doc_status": doc_status,
			"allow_edit": allow_edit,
		})
	for state, action, next_state, allowed in VENDOR_KYC_WORKFLOW_TRANSITIONS:
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


def migrate_supplier_hold_to_is_frozen():
	# Suppliers created before the vendor_kyc backlink field existed never
	# got it backfilled — without it, the read_only_depends_on Property
	# Setter on Supplier.is_frozen wouldn't recognize them as vendor-
	# lifecycle Suppliers at all. Backfill from Vendor KYC's own (older)
	# `supplier` field first, so the fix below actually reaches them too.
	frappe.db.sql("""
		update `tabSupplier` s
		inner join `tabVendor KYC` k on k.supplier = s.name
		set s.vendor_kyc = k.name
		where (s.vendor_kyc is null or s.vendor_kyc = '')
	""")

	# The onboarding freeze switched from on_hold/hold_type ("Block
	# Supplier" — narrow, only some transaction types) to is_frozen (core
	# ERPNext's broader, centrally-enforced party freeze). Only touches
	# Suppliers this app itself created (vendor_kyc set) that are still on
	# the old mechanism — a Supplier a real user separately put on hold for
	# an unrelated reason is left alone.
	frappe.db.sql("""
		update `tabSupplier` set is_frozen = 1, on_hold = 0, hold_type = ''
		where vendor_kyc is not null and vendor_kyc != '' and on_hold = 1
	""")


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


def migrate_compliance_checks_to_child_table():
	# Five separate Status(+Notes) field pairs on Vendor Background Check
	# (Background Check, Sanctions/PEP, Criminal Record, Credit Check,
	# Debarment Check) were consolidated into one repeatable "Compliance
	# Checks" table, so a deployment can add its own check types later
	# without a schema change. This seeds the standard check types every
	# migrate (cheap, idempotent), then — once — carries over any non-
	# default data already sitting in the old columns.
	#
	# The old columns themselves are left as harmless orphans rather than
	# dropped here — same ImplicitCommitError reason as the other
	# migrations in this file.
	_ensure_compliance_check_types()
	_ensure_default_compliance_check_template()

	if not frappe.db.has_column("Vendor Background Check", "background_check_status"):
		return

	rows = frappe.db.sql(
		"""
		select name, background_check_status, background_check_notes, background_check_report,
			   sanctions_screening_status, sanctions_screening_notes,
			   criminal_record_check_status, criminal_record_check_notes,
			   credit_check_status, credit_check_score, credit_check_source, credit_check_notes,
			   debarment_check_status, debarment_check_notes
		from `tabVendor Background Check`
		""",
		as_dict=True,
	)
	for row in rows:
		# (check_type, status, notes, source, reference_value, attachment)
		checks = [
			("Background Check", row.background_check_status, row.background_check_notes, None, None, row.background_check_report),
			("Sanctions / PEP Screening", row.sanctions_screening_status, row.sanctions_screening_notes, None, None, None),
			("Criminal / Court Record Check", row.criminal_record_check_status, row.criminal_record_check_notes, None, None, None),
			("Credit / Financial Standing Check", row.credit_check_status, row.credit_check_notes, row.credit_check_source, row.credit_check_score, None),
			("Regulatory Debarment / Blacklist Check", row.debarment_check_status, row.debarment_check_notes, None, None, None),
		]
		has_data = any(
			(status and status not in ("Not Started", "")) or notes or source or reference_value or attachment
			for _check_type, status, notes, source, reference_value, attachment in checks
		)
		if not has_data:
			continue

		doc = frappe.get_doc("Vendor Background Check", row.name)
		for check_type, status, notes, source, reference_value, attachment in checks:
			doc.append("compliance_checks", {
				"check_type": check_type,
				"status": status or "Not Started",
				"notes": notes,
				"source": source,
				"reference_value": reference_value,
				"attachment": attachment,
			})
		doc.save(ignore_permissions=True)


SETTINGS_FIELD_DEFAULTS = {
	"duplicate_request_handling": "Warn",
	"require_onboarding_request_for_kyc": 1,
	"duplicate_kyc_handling": "Stop",
	"require_complete_bank_details": 1,
	"require_complete_address_details": 1,
	"background_check_mandatory": 1,
	"reference_result_method": "Average",
	"reference_minimum_average_rating": 3,
	"background_check_result_method": "Each Reference Must Pass",
	"compliance_audit_validity_months": 12,
}


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
	for fieldname, default in SETTINGS_FIELD_DEFAULTS.items():
		if not frappe.db.exists("Singles", {"doctype": "Vendor Lifecycle Settings", "field": fieldname}):
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


def backfill_last_satisfaction_survey_date():
	# Supplier.last_satisfaction_survey_date only starts getting kept in
	# sync going forward (see VendorSatisfactionSurvey.after_insert) — this
	# fills in real history for surveys that already existed before that
	# field did, so a vendor surveyed last week doesn't look never-surveyed
	# to the scheduler and get an unwanted extra one today. Only fills in
	# where still blank — never overwrites a value the app itself set.
	rows = frappe.db.sql(
		"""
		select vendor, max(survey_date) as last_date
		from `tabVendor Satisfaction Survey`
		where vendor is not null and vendor != ''
		group by vendor
		""",
		as_dict=True,
	)
	for row in rows:
		if not frappe.db.exists("Supplier", row.vendor):
			continue
		if not frappe.db.get_value("Supplier", row.vendor, "last_satisfaction_survey_date"):
			frappe.db.set_value("Supplier", row.vendor, "last_satisfaction_survey_date", row.last_date)


def backfill_missing_compliance_check_template():
	# migrate_compliance_checks_to_child_table() above only ever backfills
	# compliance_check_template for a Background Check that had real
	# legacy compliance data to carry over — one that simply had every old
	# status column at its default ("Not Started"/blank) was left with
	# compliance_check_template blank, same as before that migration ever
	# ran. Once the field became mandatory, that permanently blocks saving
	# such a record ever again through any path — including the internal
	# re-save recompute_overall_result() does whenever one of its
	# References changes, which otherwise fails with a MandatoryError and
	# silently leaves the Result stuck stale forever.
	#
	# Defaults to "Standard Compliance Checks" (always guaranteed to exist
	# by _ensure_default_compliance_check_template(), called earlier in
	# this same after_migrate chain) and loads that template's Check Types
	# onto it, so the record is immediately usable rather than merely
	# unblocked. Only touches drafts — a submitted or cancelled Background
	# Check is locked either way and re-saving it here would serve no
	# purpose.
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_background_check.vendor_background_check import (
		recompute_overall_result,
	)

	affected = frappe.get_all(
		"Vendor Background Check",
		filters={"compliance_check_template": ["in", ["", None]], "docstatus": 0},
		pluck="name",
	)
	for name in affected:
		doc = frappe.get_doc("Vendor Background Check", name)
		doc.compliance_check_template = "Standard Compliance Checks"
		if not doc.compliance_checks:
			doc.load_compliance_checks_from_template()
		doc.save(ignore_permissions=True)
		# Saving the Background Check itself never recomputes its own
		# Result — that only ever happens from the Reference side. Now
		# that saving is unblocked, force one recompute so a Result that
		# went stale while this record was stuck (e.g. a Reference was
		# added or changed in the meantime) is corrected immediately,
		# rather than waiting for the next unrelated Reference event.
		recompute_overall_result(name)


def backfill_result_method_resolved():
	# "result_method_resolved" was added alongside the fix that made
	# average_rating hide itself for non-"Average" methods — a Background
	# Check whose Result was computed before that field existed never got
	# it backfilled, and it only ever gets set again by
	# recompute_overall_result() (triggered by a Reference event, which
	# doesn't fire again for an unrelated field just sitting there stale).
	# Left as None forever, check_result_is_fresh() would then permanently
	# see it as different from a fresh computation — even when nothing
	# about the actual Result has changed — blocking submission of an
	# otherwise perfectly valid draft in an unbreakable loop.
	#
	# Only backfills rows where a fresh recomputation exactly matches the
	# Result already stored — if it's drifted for some other reason, this
	# leaves it alone rather than silently overwriting a genuine staleness
	# with a plausible-looking value.
	from vendor_lifecycle.vendor_lifecycle.doctype.vendor_background_check.vendor_background_check import (
		_compute_aggregate,
	)

	affected = frappe.get_all(
		"Vendor Background Check",
		filters={"result_method_resolved": ["is", "not set"]},
		fields=["name", "outcome"],
	)
	for row in affected:
		if not row.outcome:
			continue
		fresh_result, fresh_method, _references = _compute_aggregate(row.name)
		if fresh_result == row.outcome:
			frappe.db.set_value(
				"Vendor Background Check", row.name, "result_method_resolved", fresh_method, update_modified=False
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


def rename_estimated_monthly_capacity():
	# "Estimated Monthly Capacity" was renamed to the more specific
	# "Estimated Monthly Production Capacity" on both Vendor Onboarding
	# Request and Vendor KYC (they've always carried this as two
	# independent, identically-named fields — see COMMON_BUSINESS_DETAIL_
	# FIELDS and start_kyc()'s field copy). frappe.rename_field() only
	# copies data into the new column; the old column is left as a
	# harmless orphan, same as every other rename in this file.
	from frappe.model.utils.rename_field import rename_field

	for doctype in ("Vendor Onboarding Request", "Vendor KYC"):
		if frappe.db.has_column(doctype, "estimated_monthly_capacity"):
			rename_field(doctype, "estimated_monthly_capacity", "estimated_monthly_production_capacity")


def migrate_facility_applicable_to_yes_no():
	# "Facility Applicable" was a plain checkbox (0/1, defaulting to 1); it's
	# now a mandatory Yes/No dropdown that also governs whether Facility
	# Area/Unit get zeroed out. Existing rows still hold '1'/'0' (or blank,
	# on very old rows) — converted to the new option strings so an
	# already-answered record keeps showing (or hiding) its Facility Area
	# section exactly as it did before, rather than looking like the data
	# vanished. Idempotent: safe to run on every migrate.
	frappe.db.sql("update `tabVendor Compliance Audit` set facility_applicable = 'Yes' where facility_applicable = '1'")
	frappe.db.sql(
		"update `tabVendor Compliance Audit` set facility_applicable = 'No'"
		" where facility_applicable in ('0', '') or facility_applicable is null"
	)


def backfill_license_insurance_masters():
	# license_type/issuing_authority/insurance_type/insurer were converted
	# from free-text Data fields to Link fields pointing at new master
	# doctypes — any value already typed into an existing row needs a
	# matching master record created for it, or that row's Link would show
	# as invalid/unresolvable going forward. Idempotent: only ever creates
	# what's missing.
	_backfill_master_from_columns("License Type", "license_type_name", [
		("Vendor Compliance Audit License", "license_type"),
		("Licenses and Permits Template Item", "license_type"),
	])
	_backfill_master_from_columns("Issuing Authority", "issuing_authority_name", [
		("Vendor Compliance Audit License", "issuing_authority"),
	])
	_backfill_master_from_columns("Insurance Type", "insurance_type_name", [
		("Vendor Compliance Audit Insurance", "insurance_type"),
		("Insurance Template Item", "insurance_type"),
	])
	_backfill_master_from_columns("Insurer", "insurer_name", [
		("Vendor Compliance Audit Insurance", "insurer"),
	])


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


def _backfill_master_from_columns(master_doctype, master_fieldname, source_columns):
	values = set()
	for source_doctype, fieldname in source_columns:
		if not frappe.db.has_column(source_doctype, fieldname):
			continue
		values.update(
			frappe.db.sql_list(
				f"select distinct `{fieldname}` from `tab{source_doctype}`"
				f" where `{fieldname}` is not null and `{fieldname}` != ''"
			)
		)

	for value in values:
		if not frappe.db.exists(master_doctype, value):
			frappe.get_doc({"doctype": master_doctype, master_fieldname: value}).insert(ignore_permissions=True)


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


DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE = "Vendor Support Ticket Created"
DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE = "Vendor Support Ticket New Ticket Alert"
DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE = "Vendor Support Ticket Resolved"
DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE = "Vendor Support Ticket Reopened"
DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE = "Vendor Support Ticket Escalation"


def backfill_default_support_ticket_email_templates():
	# Referenced directly by name in vendor_support_ticket.py / tasks.py,
	# not a Settings field — same "hardcoded, backfilled once" convention
	# as every other Vendor Lifecycle template. A deployment is free to
	# edit any of these afterward.
	if not frappe.db.exists("Email Template", DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>We've received your support ticket <b>{{ ticket_name }}</b>: \"{{ subject }}\""
			" (Priority: {{ priority }}).</p>"
			'<p><a href="{{ ticket_link }}">View the ticket</a> to track its progress or add more details.</p>'
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SUPPORT_TICKET_CREATED_EMAIL_TEMPLATE,
			"subject": "We've Received Your Ticket — {{ ticket_name }}",
			"response": _signoff_email_shell(SIGNOFF_RECEIVED_ACCENT, "Received", "Ticket Received", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE):
		body = (
			"<p>Dear {{ contact_person_name or firm_name }},</p>"
			"<p>Your support ticket <b>{{ ticket_name }}</b>: \"{{ subject }}\" has been resolved.</p>"
			+ _info_box(SIGNOFF_PASSED_ACCENT, "{{ resolution }}")
			+ '<p><a href="{{ ticket_link }}">View the ticket</a> — close it if you\'re satisfied, or reopen it'
			" if it isn't actually fixed.</p>"
			"<p>Thank you,<br>{{ company_name }}</p>"
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SUPPORT_TICKET_RESOLVED_EMAIL_TEMPLATE,
			"subject": "Your Ticket Has Been Resolved — {{ ticket_name }}",
			"response": _signoff_email_shell(SIGNOFF_PASSED_ACCENT, "Resolved", "Ticket Resolved", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>Support ticket <b>{{ ticket_name }}</b>: \"{{ subject }}\" for <b>{{ firm_name }}</b> has been"
			" reopened by the vendor.</p>"
			'<p><a href="{{ ticket_link }}">View the ticket</a> and follow up.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SUPPORT_TICKET_REOPENED_EMAIL_TEMPLATE,
			"subject": "Ticket Reopened — {{ ticket_name }}",
			"response": _signoff_email_shell(VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "Ticket Reopened", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p><b>{{ firm_name }}</b> has filed a new support ticket <b>{{ ticket_name }}</b>: \"{{ subject }}\""
			" (Priority: {{ priority }}).</p>"
			'<p><a href="{{ ticket_link }}">View the ticket</a> and follow up.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SUPPORT_TICKET_NEW_TICKET_ALERT_EMAIL_TEMPLATE,
			"subject": "New Support Ticket — {{ ticket_name }}",
			"response": _signoff_email_shell(VENDOR_LIFECYCLE_INTERNAL_ACCENT, "Action Needed", "New Support Ticket", body),
		}).insert(ignore_permissions=True)

	if not frappe.db.exists("Email Template", DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE):
		body = (
			"<p>Hello,</p>"
			"<p>Support ticket <b>{{ ticket_name }}</b>: \"{{ subject }}\" for <b>{{ firm_name }}</b>"
			" (Priority: {{ priority }}) has been sitting without a response.</p>"
			'<p><a href="{{ ticket_link }}">View the ticket</a> and follow up.</p>'
		)
		frappe.get_doc({
			"doctype": "Email Template",
			"name": DEFAULT_SUPPORT_TICKET_ESCALATION_EMAIL_TEMPLATE,
			"subject": "Ticket Needs Attention — {{ ticket_name }}",
			"response": _signoff_email_shell(SIGNOFF_FAILED_ACCENT, "Escalation", "Ticket Needs Attention", body),
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


def migrate_is_resolvable_check_to_select():
	# is_resolvable was a plain Check (0/1); it's now a mandatory Select
	# (Yes/No/Maybe) so a value has to be a deliberate choice, not a silent
	# default. The old boolean column just becomes varchar in place, so
	# existing rows are left holding the literal strings "0"/"1" — neither
	# is a valid option. "1" maps to "Yes"; "0" is cleared to blank rather
	# than mapped to "No", since it was never a deliberate answer (it was
	# every row's untouched default).
	rows = frappe.db.sql(
		"select name, is_resolvable from `tabVendor Deboarding Request` where is_resolvable in ('0', '1')",
		as_dict=True,
	)
	for row in rows:
		frappe.db.set_value(
			"Vendor Deboarding Request", row.name, "is_resolvable", "Yes" if row.is_resolvable == "1" else ""
		)


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
