// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Sampling Evaluation", {
	before_submit(frm) {
		// Block by default; only lift it once the server confirms this
		// document is actually ready — same pattern as Vendor Background
		// Check / Vendor Compliance Audit's own before_submit.
		frappe.validated = false;
		return frm.call("check_submit_readiness").then((r) => {
			const result = r.message || {};

			if (result.structural_error) {
				frappe.msgprint({
					title: __("Cannot Submit Yet"),
					indicator: "red",
					message: result.structural_error,
				});
				return; // frappe.validated stays false
			}

			// Always confirm before submitting — the message makes the
			// consequence explicit (Rejected disables the vendor), so the
			// reviewer isn't surprised by a side effect they didn't see
			// coming.
			let message = result.evaluation_outcome === "Rejected"
				? __("Reject and submit this Sampling Evaluation? The vendor will be disabled.")
				: __("Approve and submit this Sampling Evaluation?");

			// A warning, not a block — the same save-time msgprint already
			// flagged this; the confirm dialog repeats it here since it's
			// the reviewer's last chance to back out before the outcome
			// becomes final.
			if (result.has_unreceived_samples) {
				message += " " + __("Note: one or more samples are still Not Received or In-Transit.");
			}

			return new Promise((resolve) => {
				frappe.confirm(
					message,
					() => {
						frappe.validated = true;
						resolve();
					},
					() => {
						frappe.validated = false;
						resolve();
					}
				);
			});
		});
	},
	refresh(frm) {
		// "Create Sampling Evaluation" (from Vendor KYC's "Create" dropdown,
		// or the per-stage buttons elsewhere) opens a new document via
		// frappe.new_doc(doctype, {kyc: ...}) — that prefill mechanism
		// (route_options) sets the field with a raw property assignment,
		// not frm.set_value(), so it never fires a "kyc changed" trigger
		// and vendor (server-side auto-synced from the KYC's Supplier via
		// sync_vendor_field, but only ever computed when the document is
		// actually saved) is still blank the moment the form first
		// renders. Since vendor is both mandatory and read-only, Frappe's
		// own client-side "fill in mandatory fields" check would
		// otherwise block the very first save attempt before the server
		// ever gets a chance to fill it in — an unbreakable dead end, since
		// the user has no way to type into a read-only field themselves.
		// refresh() always fires regardless of how kyc got its value, so
		// this fills vendor in immediately, before any save is attempted.
		// Same pattern as Vendor Background Check / Vendor Compliance Audit.
		if (frm.is_new() && frm.doc.kyc && !frm.doc.vendor) {
			frappe.db.get_value("Vendor KYC", frm.doc.kyc, "supplier").then((r) => {
				if (r.message && r.message.supplier) {
					frm.set_value("vendor", r.message.supplier);
				}
			});
		}

		toggle_evaluation_results_add_row(frm);

		// Same treatment as a Failed Vendor Background Check / Vendor
		// Compliance Audit's own banner.
		if (frm.doc.docstatus === 1 && frm.doc.evaluation_outcome === "Rejected") {
			frm.dashboard.clear_headline();
			if (frm.doc.force_overridden) {
				frm.set_intro(
					__("This Rejected result was force-overridden — reason: {0}", [frm.doc.force_override_reason]),
					"orange"
				);
			} else {
				frm.set_intro(
					__(
						"This Sampling Evaluation was Rejected — the next onboarding stages cannot proceed," +
							" and the vendor's Supplier record has been disabled."
					),
					"red"
				);
				add_force_override_button(frm);
			}
		}

		// A convenience shortcut to the next stage, right from here, once
		// this one has actually been submitted — same eligibility check
		// Vendor KYC's own "Create" dropdown uses (one active document
		// per stage per vendor, no Background Check Failure blocking
		// everything downstream, etc.), so this can never offer something
		// that would actually be rejected.
		if (frm.doc.docstatus === 1 && frm.doc.kyc) {
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.stage_sequencing.get_available_stages",
				args: { kyc: frm.doc.kyc },
			}).then((r) => {
				const stages = (r.message && r.message.stages) || [];
				if (stages.includes("Vendor Sign Off")) {
					frm.add_custom_button(__("Sign-off"), () => {
						frappe.new_doc("Vendor Sign Off", { kyc: frm.doc.kyc });
					}, __("Create"));
					// Same primary-blue styling as Vendor KYC's own "Create"
					// dropdown.
					frm.page.set_inner_btn_group_as_primary(__("Create"));
				}
			});
		}
	},

	evaluation_template(frm) {
		toggle_evaluation_results_add_row(frm);
		if (frm.doc.evaluation_template) {
			frm.call("load_evaluation_from_template").then(() => {
				frm.refresh_field("evaluation_results");
			});
		} else {
			// Rows loaded from the just-removed template no longer mean
			// anything (their Criteria/Category came from that template, not
			// from hand entry) — cleared out rather than left sitting there
			// as stale, now-editable leftovers. Same pattern as Vendor
			// Compliance Audit's own checklist_template handler.
			frm.clear_table("evaluation_results");
			frm.refresh_field("evaluation_results");
		}
	},
});

function add_force_override_button(frm) {
	// Hardcoded to these two roles, by explicit product decision — not a
	// Vendor Lifecycle Settings field (same reasoning as Vendor KYC's own
	// freeze/unfreeze roles). force_override() re-checks this same
	// permission server-side; this is only what decides whether the
	// button is even shown.
	const canOverride = frappe.user.has_role("System Manager") || frappe.user.has_role("Vendor Lifecycle Manager");
	if (!canOverride || frm.doc.force_overridden) return;

	frm.add_custom_button(__("Force Override"), () => {
		frappe.prompt(
			[{ fieldname: "reason", fieldtype: "Small Text", label: __("Force Override Reason"), reqd: 1 }],
			(values) => {
				frm.call("force_override", { reason: values.reason }).then(() => frm.reload_doc());
			},
			__("Force Override This Result"),
			__("Override")
		);
	}).addClass("btn-danger");
}

function toggle_evaluation_results_add_row(frm) {
	// A template's evaluation criteria are meant to be used as-is — once
	// one is picked, rows can no longer be added, removed, or duplicated
	// by hand, and Criteria/Category (which came straight from the
	// template) can no longer be hand-edited either; clearing the
	// template unlocks the grid again, including Criteria/Category, so
	// rows can be entered by hand. Same mechanism as Vendor Compliance
	// Audit's toggle_checklist_items_add_row — must go through
	// set_df_property / update_docfield_property (not a direct grid
	// assignment), since the grid's own Delete/Duplicate buttons and
	// row-field read_only read these off the field's docfield object.
	const locked = !!frm.doc.evaluation_template;
	frm.set_df_property("evaluation_results", "cannot_add_rows", locked);
	frm.set_df_property("evaluation_results", "cannot_delete_rows", locked);
	frm.fields_dict.evaluation_results.grid.update_docfield_property("criteria", "read_only", locked);
	frm.fields_dict.evaluation_results.grid.update_docfield_property("category", "read_only", locked);
	frm.fields_dict.evaluation_results.grid.refresh();
}
