// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Supplier", {
	refresh(frm) {
		// These connections exist only to let a reviewer trace a Supplier
		// back to the vendor-lifecycle records that reference it — none of
		// them should ever be created directly from here.
		frm.can_make_methods = frm.can_make_methods || {};
		[
			"Vendor KYC",
			"Vendor Background Check",
			"Vendor Compliance Audit",
			"Vendor Sampling Evaluation",
			"Vendor Sign Off",
			"Vendor Satisfaction Survey",
			"Vendor Support Ticket",
			"Vendor Deboarding Request",
			"Vendor Deboarding Checklist",
		].forEach((doctype) => {
			frm.can_make_methods[doctype] = () => false;
		});

		// "Disabled" is locked read-only for any vendor-lifecycle-managed
		// Supplier (see the Supplier.disabled Property Setter) — it's only
		// ever set automatically by a Failed Background Check, a Failed
		// Compliance Audit, a Rejected Sampling Evaluation, or a Failed
		// Sign Off, so if it's checked, one of those is always the reason.
		// The specific record is attached server-side as this document
		// loads (see set_supplier_disable_reason_onload, wired via
		// hooks.py doc_events) rather than fetched here with a separate
		// frappe.call — reading it straight off frm.doc.__onload is
		// synchronous, so there's no async gap for another script's own
		// refresh() to run in and touch this same intro/headline banner
		// slot first.
		const DISABLE_REASON_VERBS = {
			"Vendor Background Check": __("failed"),
			"Vendor Compliance Audit": __("failed"),
			"Vendor Sampling Evaluation": __("was rejected"),
			"Vendor Sign Off": __("failed"),
		};
		const disable_reason = frm.doc.__onload && frm.doc.__onload.disable_reason;
		if (disable_reason) {
			frm.dashboard.clear_headline();
			frm.set_intro(
				__(
					"This Supplier is disabled because {0} {1} — next onboarding stages cannot proceed" +
						" until this is resolved.",
					[
						frappe.utils.get_form_link(disable_reason.doctype, disable_reason.name, true),
						DISABLE_REASON_VERBS[disable_reason.doctype] || __("failed"),
					]
				),
				"red"
			);
		}
	},
});
