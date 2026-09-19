# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source

# Fixed label order (5 of the Vendor Reboarding Request Workflow's 6 states
# — "Cancelled" is deliberately left out of this chart) so each status
# always gets the same color regardless of which one currently has the most
# records — same reasoning as Deboarding Requests by Status's own identical
# LABELS list.
LABELS = ["In Progress", "Approval Pending", "Approved", "Rejected", "Trashed"]


@frappe.whitelist()
@cache_source
def get(
	chart_name=None,
	chart=None,
	no_cache=None,
	filters=None,
	from_date=None,
	to_date=None,
	timespan=None,
	time_interval=None,
	heatmap_year=None,
):
	counts = []
	for label in LABELS:
		counts.append(frappe.db.count("Vendor Reboarding Request", filters={"status": label}))

	return {
		"labels": [_(label) for label in LABELS],
		"datasets": [{"name": "Reboarding Requests by Status", "values": counts}],
	}
