frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Satisfaction Score by Criterion"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.satisfaction_score_by_criterion.satisfaction_score_by_criterion.get",
	filters: [],
};
