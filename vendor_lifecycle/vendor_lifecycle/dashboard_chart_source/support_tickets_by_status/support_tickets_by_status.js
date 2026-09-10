frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Support Tickets by Status"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.support_tickets_by_status.support_tickets_by_status.get",
	filters: [],
};
