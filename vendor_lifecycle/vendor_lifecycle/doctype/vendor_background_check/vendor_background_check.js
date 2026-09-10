// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Background Check", {
	refresh(frm) {
		// The Connections tab already offers its own "+" for References —
		// hide it so there's exactly one way to create one (the button
		// below), matching the rest of this app's convention.
		frm.can_make_methods = frm.can_make_methods || {};
		frm.can_make_methods["Vendor Background Check Reference"] = () => false;

		// References are permanently locked once this Background Check is no
		// longer a draft (see Reference's _require_background_check_in_draft),
		// by design, with no exceptions — including cancellation. Frappe's
		// default "Cancel" flow would otherwise try to cascade-cancel any
		// submitted References BEFORE cancelling this document, while it's
		// still submitted (docstatus 1), and that check would then correctly
		// (per its own rule) refuse. Since a submitted Background Check is
		// guaranteed to have at least one submitted Reference, that made
		// cancelling any submitted Background Check impossible. Excluding
		// References from the cascade avoids that dead end — cancelling this
		// document leaves its References as-is, still submitted, as a
		// permanent historical record.
		frm.ignore_doctypes_on_cancel_all = ["Vendor Background Check Reference"];

		// "Create Background Check" (from Vendor KYC's "Create" dropdown,
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
		if (frm.is_new() && frm.doc.kyc && !frm.doc.vendor) {
			frappe.db.get_value("Vendor KYC", frm.doc.kyc, "supplier").then((r) => {
				if (r.message && r.message.supplier) {
					frm.set_value("vendor", r.message.supplier);
				}
			});
		}

		toggle_compliance_checks_add_row(frm);

		if (frm.doc.docstatus === 1 && frm.doc.overall_status === "Failed") {
			// Core Frappe's own "Submit this document to confirm" banner
			// shares this exact same message slot — clear it first, then
			// set ours, so ours is guaranteed to be what's left showing
			// (matches the same pattern used for Vendor KYC's Rejected
			// banner).
			frm.dashboard.clear_headline();
			if (frm.doc.force_overridden) {
				frm.set_intro(
					__("This Failed result was force-overridden — reason: {0}", [frm.doc.force_override_reason]),
					"orange"
				);
			} else {
				frm.set_intro(
					__(
						"This Background Check has Failed — the next onboarding stages cannot proceed, and" +
							" the vendor's Supplier record has been disabled."
					),
					"red"
				);
				add_force_override_button(frm);
			}
		}

		// A convenience shortcut to the next stage, right from here, once
		// this one has actually passed — same eligibility check Vendor
		// KYC's own "Create" dropdown uses (one active document per
		// stage per vendor, previous stage Passed, etc.), so this can
		// never offer something that would actually be rejected.
		if (frm.doc.docstatus === 1 && frm.doc.kyc) {
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.stage_sequencing.get_available_stages",
				args: { kyc: frm.doc.kyc },
			}).then((r) => {
				const stages = (r.message && r.message.stages) || [];
				if (stages.includes("Vendor Compliance Audit")) {
					frm.add_custom_button(__("Compliance Audit"), () => {
						frappe.new_doc("Vendor Compliance Audit", { kyc: frm.doc.kyc });
					}, __("Create"));
					// Same primary-blue styling as Vendor KYC's own "Create"
					// dropdown / this form's own "Create Reference" button.
					frm.page.set_inner_btn_group_as_primary(__("Create"));
				}
			});
		}

		// Only offered once this Background Check is actually submitted
		// (billing for work still in progress doesn't make sense yet), and
		// only when an external agency was genuinely used — pre-fills
		// Supplier from the agency picked in external_agency, with the same
		// traceability fields Vendor KYC's own "Purchase Invoice" button
		// already sets.
		if (frm.doc.docstatus === 1 && frm.doc.conducted_by_external_agency && frm.doc.external_agency) {
			frm.add_custom_button(__("Bill to External Agency"), () => {
				frappe.new_doc("Purchase Invoice", {
					supplier: frm.doc.external_agency,
					vendor_lifecycle_source_doctype: frm.doc.doctype,
					vendor_lifecycle_source_name: frm.doc.name,
				});
			}, __("Create"));
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}

		// References can only ever be created while this Background Check
		// is a draft (see Reference's own _require_background_check_in_draft)
		// — a submitted OR cancelled Background Check would just have any
		// new Reference rejected the moment it tried to save, so the button
		// must not be offered in either of those states, not just submitted.
		if (frm.is_new() || frm.doc.docstatus !== 0) return;

		frm.add_custom_button(__("Create Reference"), () => {
			frappe.new_doc("Vendor Background Check Reference", { background_check: frm.doc.name });
		});
		// Same blue "primary" styling as Frappe's own standard "+ Add" buttons.
		frm.change_custom_button_type(__("Create Reference"), null, "primary");
	},

	conducted_by_external_agency(frm) {
		// Only one of the two ever applies at a time — switching clears
		// whichever side just became irrelevant, rather than leaving a
		// stale value sitting in a now-hidden field.
		if (frm.doc.conducted_by_external_agency) {
			// Table MultiSelect keeps a separate internal cache of
			// already-picked values (used to exclude them from its own
			// dropdown) that only the control's own set_value() resets -
			// neither frm.clear_table() nor frm.set_value() (the form-level
			// API) touch it, so a user removed either of those ways stays
			// invisible in the suggestions for the rest of the session even
			// though the field itself is empty again.
			frm.fields_dict.conducted_by.set_value([]);
		} else {
			frm.set_value("external_agency", "");
			frm.set_value("external_agency_contact_person", "");
			frm.set_value("external_agency_contact_number", "");
			frm.set_value("external_agency_contact_email", "");
			frm.set_value("proof_of_visit", "");
		}
	},

	compliance_check_template(frm) {
		if (frm.doc.compliance_check_template) {
			frm.call("load_compliance_checks_from_template").then(() => frm.refresh_field("compliance_checks"));
		}
	},

	before_submit(frm) {
		// Block by default; only lift it once the server confirms this
		// document is actually ready. check_submit_readiness() runs the
		// same structural checks before_submit() itself enforces — so a
		// real blocking problem (an unresolved Compliance Check, a draft
		// Reference, etc.) surfaces here FIRST, before the Overall-Status
		// confirmation below — a "Yes, submit anyway" click can no longer
		// be immediately followed by an unrelated, unrecoverable-feeling
		// failure. The server's own before_submit re-checks everything
		// again regardless (see _require_result_is_fresh and friends), so
		// this is purely about giving the user a clear, correctly-ordered
		// heads-up, not the actual enforcement.
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

			if (!result.is_fresh) {
				frappe.msgprint({
					title: __("Recalculation Needed"),
					indicator: "orange",
					message: __(
						"This Background Check's Result may be out of date — something it depends on (a" +
							" Reference, or a Vendor Lifecycle Settings value) has changed since it was last saved." +
							" Please resave, then submit again."
					),
					primary_action: {
						label: __("Resave"),
						action() {
							// frappe.msgprint's primary_action callback receives
							// the dialog's (empty, field-less) get_values() as
							// its argument, not the dialog itself — the shared
							// message dialog is closed via frappe.hide_msgprint().
							frappe.hide_msgprint();
							// What went stale here is something this doc depends on
							// (a Reference, a Settings value) — not a field on this
							// form itself, so frm.save() would otherwise see no dirty
							// fields and abort with "No changes in document" without
							// ever reaching the server. frm.dirty() forces it through
							// so the server actually recomputes.
							frm.dirty();
							frm.save();
						},
					},
				});
				return; // frappe.validated stays false
			}

			if (result.overall_status !== "Failed") {
				frappe.validated = true;
				return;
			}

			// Overall Status folds in the Compliance Checks table, not just
			// the Reference-based Result — a hard block here would prevent
			// ever recording a genuinely failed vendor, so this is a
			// confirmation, not a block: the reviewer explicitly decides.
			return new Promise((resolve) => {
				frappe.confirm(
					__(
						"This Background Check's Overall Status is Failed (based on the Result and/or" +
							" Compliance Checks). Do you still want to submit?"
					),
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

function toggle_compliance_checks_add_row(frm) {
	// Compliance Checks always come from the selected Template — rows are
	// never added, removed, or duplicated by hand, regardless of whether a
	// template has been picked yet.
	//
	// Must go through set_df_property (not a direct grid.cannot_add_rows /
	// grid.cannot_delete_rows assignment) — the grid's own Delete and
	// Duplicate button visibility checks (refresh_remove_rows_button /
	// refresh_duplicate_rows_button in Frappe's grid.js) read these two
	// flags off the field's docfield object (this.df), not off the grid
	// instance, so setting them directly on the grid instance is silently
	// ignored by exactly those two buttons.
	frm.set_df_property("compliance_checks", "cannot_add_rows", true);
	frm.set_df_property("compliance_checks", "cannot_delete_rows", true);
	frm.fields_dict.compliance_checks.grid.refresh();
}
