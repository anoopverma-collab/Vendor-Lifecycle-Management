frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Satisfaction Surveys by Status"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.satisfaction_surveys_by_status.satisfaction_surveys_by_status.get",
	filters: [],
};
