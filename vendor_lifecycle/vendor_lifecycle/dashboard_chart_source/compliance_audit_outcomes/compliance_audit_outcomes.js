frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Compliance Audit Outcomes"] = {
	method: "vendor_lifecycle.vendor_lifecycle.dashboard_chart_source.compliance_audit_outcomes.compliance_audit_outcomes.get",
	filters: [],
};
