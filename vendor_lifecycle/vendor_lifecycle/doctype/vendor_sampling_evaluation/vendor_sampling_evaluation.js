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
			// coming. An Ad-hoc evaluation is a standalone record with no
			// such consequence, either way.
			let message;
			if (frm.doc.is_renewal) {
				message =
					result.evaluation_outcome === "Rejected"
						? __("Reject and submit this Ad-hoc evaluation? This is just a record — it doesn't disable the vendor.")
						: __("Approve and submit this Ad-hoc evaluation?");
			} else {
				message = result.evaluation_outcome === "Rejected"
					? __("Reject and submit this Sampling Evaluation? The vendor will be disabled.")
					: __("Approve and submit this Sampling Evaluation?");
			}

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
		// vendor is mandatory but only editable for an Ad-hoc evaluation —
		// for Onboarding/Reboarding it's auto-derived and must arrive
		// already filled in, so every "Create" button that opens this
		// doctype (Vendor KYC's own dropdown, Vendor Reboarding Request's
		// own dropdown, Vendor Compliance Audit's own "next stage" button)
		// sets it explicitly via set_value() once this form is confirmed to
		// exist — no lookup needed here at all.

		// Same reasoning as Vendor Compliance Audit / Vendor Sign Off's own
		// identical refresh() check — Onboarding/Reboarding are only ever
		// legitimate when they arrive from one of those same "Create"
		// buttons (kyc/reboarding_request already set — sampling_type's own
		// read_only_depends_on then locks the field). Opened any other way
		// (the plain "+ New" button), the only thing left that ever makes
		// sense to pick by hand is Ad-hoc.
		if (frm.is_new() && !frm.doc.kyc && !frm.doc.reboarding_request) {
			frm.set_df_property("sampling_type", "options", "\nAd-hoc");
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
			} else if (frm.doc.is_renewal) {
				// An Ad-hoc evaluation isn't gating anything downstream, and
				// never has a Force Override option — see
				// stage_sequencing.force_override_stage's own is_renewal
				// check.
				frm.set_intro(
					__("This Ad-hoc evaluation was Rejected — it's just a record, the vendor was not disabled."),
					"red"
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
		// this one has actually been submitted. Re-boarding doesn't go
		// through get_available_stages() at all — that's onboarding-only,
		// kyc-scoped sequencing (see stage_sequencing.enforce_sequential_
		// creation's is_reboarding branch, which no-ops entirely for a
		// re-boarding document) — so a re-boarding-flagged Sampling
		// Evaluation always offers this button once submitted, the same
		// as Vendor Reboarding Request's own "Create" dropdown does; a
		// duplicate/already-retried attempt is still correctly refused
		// server-side (see Vendor Sign Off's own _require_no_active_
		// signoff_unless_failed).
		if (frm.doc.docstatus === 1 && frm.doc.is_reboarding) {
			// Don't offer a Sign-off that would just be refused server-side
			// — a draft one already exists, a submitted one that isn't
			// Failed already exists (sign_off_available), or Sign-off's
			// own mandatory predecessor hasn't passed yet
			// (missing_requirement) — same checks Vendor Reboarding
			// Request's own "Create" dropdown already respects.
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.doctype.vendor_reboarding_request.vendor_reboarding_request.get_reboarding_stage_info_for",
				args: { reboarding_request: frm.doc.reboarding_request },
			}).then((r) => {
				const info = r.message || {};
				if (!info.sign_off_available) return;
				if ((info.missing_requirement || {})["Vendor Sign Off"]) return;
				frm.add_custom_button(info.sign_off_is_retry ? __("Retry Sign-off") : __("Sign-off"), () => {
					const vendor = frm.doc.vendor;
					// See Vendor KYC's own "Create" dropdown for why vendor
					// is set this way, explicitly, once the new form is
					// confirmed to exist — handing it over as a plain
					// route_options value isn't reliable enough on its own
					// for a mandatory field.
					frappe.new_doc("Vendor Sign Off", {
						reboarding_request: frm.doc.reboarding_request,
						signoff_type: "Reboarding",
					}).then(() => {
						if (cur_frm && cur_frm.doctype === "Vendor Sign Off" && cur_frm.is_new()) {
							cur_frm.set_value("vendor", vendor);
						}
					});
				}, __("Create"));
				frm.page.set_inner_btn_group_as_primary(__("Create"));
			});
		} else if (frm.doc.docstatus === 1 && frm.doc.kyc) {
			// Same eligibility check Vendor KYC's own "Create" dropdown
			// uses (one active document per stage per vendor, no
			// Background Check Failure blocking everything downstream,
			// etc.), so this can never offer something that would
			// actually be rejected.
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.stage_sequencing.get_available_stages",
				args: { kyc: frm.doc.kyc },
			}).then((r) => {
				const stages = (r.message && r.message.stages) || [];
				if (stages.includes("Vendor Sign Off")) {
					frm.add_custom_button(__("Sign-off"), () => {
						const vendor = frm.doc.vendor;
						frappe.new_doc("Vendor Sign Off", {
							kyc: frm.doc.kyc,
							signoff_type: "Onboarding",
						}).then(() => {
							if (cur_frm && cur_frm.doctype === "Vendor Sign Off" && cur_frm.is_new()) {
								cur_frm.set_value("vendor", vendor);
							}
						});
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

	frm.add_custom_button(__("Force Override Status"), () => {
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
