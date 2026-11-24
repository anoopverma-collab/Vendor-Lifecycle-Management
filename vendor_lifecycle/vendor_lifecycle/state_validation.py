# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe

# Shared by Vendor KYC and Vendor Onboarding Request — both have a
# state_master (Link to the "State" master, filtered by country on the
# client) feeding a plain-text state field via fetch_from, so the doctype
# itself always ends up storing the bare official state name (e.g.
# "Karnataka") rather than State's own compound docname (e.g.
# "Karnataka (India)") — a Link's docname would break india_compliance's
# own Address controller and GSTIN validation, both of which expect the
# plain name.


def validate_state_master_matches_country(doc):
	if not doc.state_master:
		return
	state_country = frappe.db.get_value("State", doc.state_master, "country")
	if doc.country and state_country != doc.country:
		frappe.throw(
			frappe._("{0} belongs to {1}, not {2}. Pick a State that matches the selected Country.").format(
				frappe.bold(doc.state), frappe.bold(state_country), frappe.bold(doc.country)
			)
		)


def require_state_for_india(doc):
	if doc.country == "India" and not doc.state:
		frappe.throw(frappe._("State is mandatory for India."), frappe.MandatoryError)


def validate_indian_state_spelling(doc):
	# Fallback for when state arrived without going through the State
	# dropdown (state_master) — e.g. a direct API call, an import, or the
	# "Start KYC" pre-fill — normalizes case and rejects an unofficial
	# spelling, the same guarantee the dropdown would have given by
	# construction.
	if (
		doc.state_master
		or doc.country != "India"
		or not doc.state
		or "india_compliance" not in frappe.get_installed_apps()
	):
		return

	from india_compliance.gst_india.constants import STATE_NUMBERS

	match = next((s for s in STATE_NUMBERS if s.lower() == doc.state.strip().lower()), None)
	if not match:
		frappe.throw(
			frappe._("{0} is not a valid Indian state. Please select a valid state.").format(frappe.bold(doc.state))
		)
	doc.state = match
