frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Support Tickets by Priority"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.support_tickets_by_priority.support_tickets_by_priority.get",
	filters: [],
};
