frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Reboarding Requests by Status"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.reboarding_requests_by_status.reboarding_requests_by_status.get",
	filters: [],
};
