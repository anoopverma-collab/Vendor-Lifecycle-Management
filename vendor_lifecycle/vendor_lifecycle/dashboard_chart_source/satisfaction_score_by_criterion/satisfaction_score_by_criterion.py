# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.dashboard import cache_source

from vendor_lifecycle.vendor_lifecycle.permissions import internal_only

from vendor_lifecycle.vendor_lifecycle.engagement_dashboard import average_score_by_criterion


@frappe.whitelist()
@internal_only
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
	return average_score_by_criterion()
