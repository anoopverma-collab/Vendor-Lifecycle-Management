# Copyright (c) 2026, Auriga IT and contributors
# For license information, please see license.txt

"""Number cards and chart data for the Vendor Engagement dashboard.

Everything here looks at Vendor Satisfaction Surveys whose survey date falls
in the last WINDOW_MONTHS months (rolling from today - Frappe's own "Previous
1 year" filter means the last calendar year instead). Scores are stored 0-1
by the Rating field and shown out of 5, the same as the stars.
"""

import frappe
from frappe.query_builder.functions import Avg, Count
from frappe.utils import add_months, flt, nowdate

from vendor_lifecycle.vendor_lifecycle.permissions import require_internal_user

SURVEY = "Vendor Satisfaction Survey"
RATING = "Vendor Satisfaction Rating"
WINDOW_MONTHS = 12
STARS = 5


def _window_start():
	# Site-wide figures — internal users only (a Supplier can read its own
	# surveys, so the read check alone isn't enough).
	require_internal_user()
	frappe.has_permission(SURVEY, "read", throw=True)
	return add_months(nowdate(), -WINDOW_MONTHS)


def _completed_ratings_query():
	survey = frappe.qb.DocType(SURVEY)
	rating = frappe.qb.DocType(RATING)
	return (
		frappe.qb.from_(rating)
		.join(survey)
		.on((rating.parent == survey.name) & (rating.parenttype == SURVEY))
		.where((survey.docstatus == 1) & (survey.survey_date >= _window_start()))
	), rating


@frappe.whitelist()
def completed_surveys(filters=None):
	return frappe.db.count(SURVEY, {"docstatus": 1, "survey_date": [">=", _window_start()]})


@frappe.whitelist()
def response_rate(filters=None):
	start = _window_start()
	total = frappe.db.count(SURVEY, {"docstatus": ["<", 2], "survey_date": [">=", start]})
	completed = frappe.db.count(SURVEY, {"docstatus": 1, "survey_date": [">=", start]})
	return {"value": flt(completed * 100 / total, 1) if total else 0, "fieldtype": "Percent"}


@frappe.whitelist()
def average_score(filters=None):
	query, rating = _completed_ratings_query()
	average = query.select(Avg(rating.score)).run()[0][0]
	return {"value": flt((average or 0) * STARS, 2), "fieldtype": "Float"}


def average_score_by_criterion():
	query, rating = _completed_ratings_query()
	rows = (
		query.select(rating.criteria, Avg(rating.score), Count("*"))
		.groupby(rating.criteria)
		.orderby(rating.criteria)
		.run()
	)
	return {
		"labels": [criteria for criteria, _average, _count in rows],
		"datasets": [
			{
				"name": frappe._("Average Score (out of 5)"),
				"values": [flt(average * STARS, 2) for _criteria, average, _count in rows],
			}
		],
	}
