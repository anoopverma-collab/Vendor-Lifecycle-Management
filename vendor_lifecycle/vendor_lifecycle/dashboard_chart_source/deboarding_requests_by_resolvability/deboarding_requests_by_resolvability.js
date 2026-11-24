frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Deboarding Requests by Resolvability"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.deboarding_requests_by_resolvability.deboarding_requests_by_resolvability.get",
	filters: [],
};
