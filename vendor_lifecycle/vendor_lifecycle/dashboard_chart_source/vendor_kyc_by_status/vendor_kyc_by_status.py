# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source

# Fixed label order so each status always gets the same color regardless of
# which one currently has the most records - Frappe's built-in "Group By"
# chart type always orders slices by live count (order_by="count desc" in
# frappe/desk/doctype/dashboard_chart/dashboard_chart.py), which made a
# fixed color-per-status mapping impossible to keep stable. The colors
# themselves live on the Dashboard Chart record's own custom_options field
# (static, not something this function can vary per request) - so labels
# and values below always stay full-length and in this exact order, never
# filtered, or they'd shift out of alignment with those fixed colors.
LABELS = ['Approved', 'Rejected', 'In Progress', 'Approval Pending']
DISPLAY_LABELS = ['Approved', 'Rejected', 'In Progress', 'Pending']


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
		counts.append(frappe.db.count("Vendor KYC", filters={"status": label}))

	return {
		"labels": [_(label) for label in DISPLAY_LABELS],
		"datasets": [{"name": "Vendor KYC by Status", "values": counts}],
	}
