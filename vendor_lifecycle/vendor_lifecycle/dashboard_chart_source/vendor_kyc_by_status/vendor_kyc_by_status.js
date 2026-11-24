frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Vendor KYC by Status"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.vendor_kyc_by_status.vendor_kyc_by_status.get",
	filters: [],
};
