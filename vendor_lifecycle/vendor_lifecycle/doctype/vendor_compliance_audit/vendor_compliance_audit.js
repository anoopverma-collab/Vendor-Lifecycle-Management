// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Compliance Audit", {
	refresh(frm) {
		// vendor is mandatory but only editable for a Renewal — for
		// Onboarding/Reboarding it's auto-derived and must arrive already
		// filled in, so every "Create" button that opens this doctype
		// (Vendor KYC's own dropdown, Vendor Reboarding Request's own
		// dropdown, Vendor Background Check's own "next stage" button)
		// resolves vendor itself, synchronously, before opening this form —
		// no lookup needed here at all.

		// Onboarding/Reboarding are only ever legitimate when they arrive
		// from one of those same "Create" buttons (kyc/reboarding_request
		// already set — audit_type's own read_only_depends_on then locks
		// the field so it can't be hand-changed away from that). Opened
		// any other way (the plain "+ New" button, with no such context),
		// the only thing left that ever makes sense to pick by hand is
		// Renewal — there's no KYC/Reboarding Request to attach an
		// Onboarding/Reboarding-flagged document to in that case.
		if (frm.is_new() && !frm.doc.kyc && !frm.doc.reboarding_request) {
			frm.set_df_property("audit_type", "options", "\nRenewal");
		}

		toggle_checklist_items_add_row(frm);
		toggle_licenses_add_row(frm);
		toggle_insurance_certificates_add_row(frm);

		// Coverage Breakdown rows only make sense for an Insurance Type
		// already added above — get_query is a function, re-evaluated live
		// every time the picker opens, so this always reflects whatever's
		// currently in Insurance Certificates without needing to be reset
		// on every row add/remove.
		frm.fields_dict.insurance_perils.grid.get_field("insurance_type").get_query = () => {
			const types = (frm.doc.insurance_certificates || []).map((row) => row.insurance_type).filter(Boolean);
			return { filters: { name: ["in", types.length ? types : [""]] } };
		};

		// Same treatment as a Failed Vendor Background Check's own banner.
		if (frm.doc.docstatus === 1 && frm.doc.outcome === "Failed") {
			frm.dashboard.clear_headline();
			if (frm.doc.force_overridden) {
				frm.set_intro(
					__("This Failed result was force-overridden — reason: {0}", [frm.doc.force_override_reason]),
					"orange"
				);
			} else if (frm.doc.is_renewal) {
				// A Renewal isn't gating anything downstream, and never has
				// a Force Override option — see stage_sequencing.
				// force_override_stage's own is_renewal check.
				frm.set_intro(
					__(
						"This Renewal has Failed. Whether the vendor's Supplier record was disabled because of" +
							" this depends on the \"Disable Vendor on Compliance Audit Expiry/Failure\" setting."
					),
					"red"
				);
			} else {
				frm.set_intro(
					__(
						"This Compliance Audit has Failed — the next onboarding stages cannot proceed, and" +
							" the vendor's Supplier record has been disabled."
					),
					"red"
				);
				add_force_override_button(frm);
			}
		}

		// A convenience shortcut to the next stage, right from here, once
		// this one has actually passed. Re-boarding doesn't go through
		// get_available_stages() at all — that's onboarding-only,
		// kyc-scoped sequencing (see stage_sequencing.enforce_sequential_
		// creation's is_reboarding branch, which no-ops entirely for a
		// re-boarding document) — so a re-boarding-flagged Compliance
		// Audit always offers this button once submitted, the same as
		// Vendor Reboarding Request's own "Create" dropdown does; a
		// duplicate attempt is still correctly refused server-side.
		if (frm.doc.docstatus === 1 && frm.doc.is_reboarding) {
			// Don't offer this once re-boarding is already done, or while
			// Sampling Evaluation's own mandatory predecessor hasn't
			// passed yet — see Vendor Background Check's own identical
			// check.
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.doctype.vendor_reboarding_request.vendor_reboarding_request.get_reboarding_stage_info_for",
				args: { reboarding_request: frm.doc.reboarding_request },
			}).then((r) => {
				const info = r.message || {};
				if (info.reboarding_complete) return;
				if ((info.missing_requirement || {})["Vendor Sampling Evaluation"]) return;
				frm.add_custom_button(__("Sampling Evaluation"), () => {
					const vendor = frm.doc.vendor;
					// See Vendor KYC's own "Create" dropdown for why vendor
					// is set this way, explicitly, once the new form is
					// confirmed to exist — handing it over as a plain
					// route_options value isn't reliable enough on its own
					// for a mandatory field.
					frappe.new_doc("Vendor Sampling Evaluation", {
						reboarding_request: frm.doc.reboarding_request,
						sampling_type: "Reboarding",
					}).then(() => {
						if (cur_frm && cur_frm.doctype === "Vendor Sampling Evaluation" && cur_frm.is_new()) {
							cur_frm.set_value("vendor", vendor);
						}
					});
				}, __("Create"));
				frm.page.set_inner_btn_group_as_primary(__("Create"));
			});
		} else if (frm.doc.docstatus === 1 && frm.doc.kyc) {
			// Same eligibility check Vendor KYC's own "Create" dropdown
			// uses (one active document per stage per vendor, this stage
			// Passed, no Background Check Failure blocking everything
			// downstream, etc.), so this can never offer something that
			// would actually be rejected.
			frappe.call({
				method: "vendor_lifecycle.vendor_lifecycle.stage_sequencing.get_available_stages",
				args: { kyc: frm.doc.kyc },
			}).then((r) => {
				const stages = (r.message && r.message.stages) || [];
				if (stages.includes("Vendor Sampling Evaluation")) {
					frm.add_custom_button(__("Sampling Evaluation"), () => {
						const vendor = frm.doc.vendor;
						frappe.new_doc("Vendor Sampling Evaluation", {
							kyc: frm.doc.kyc,
							sampling_type: "Onboarding",
						}).then(() => {
							if (cur_frm && cur_frm.doctype === "Vendor Sampling Evaluation" && cur_frm.is_new()) {
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

		// Only offered once this Audit is actually submitted (billing for
		// work still in progress doesn't make sense yet), and only when an
		// external agency was genuinely used — pre-fills Supplier from the
		// agency picked in external_auditor, with the same traceability
		// fields Vendor KYC's own "Purchase Invoice" button already sets.
		if (frm.doc.docstatus === 1 && frm.doc.conducted_by_external_agency && frm.doc.external_auditor) {
			frm.add_custom_button(__("Bill to External Agency"), () => {
				frappe.new_doc("Purchase Invoice", {
					supplier: frm.doc.external_auditor,
					vendor_lifecycle_source_doctype: frm.doc.doctype,
					vendor_lifecycle_source_name: frm.doc.name,
				});
			}, __("Create"));
			frm.page.set_inner_btn_group_as_primary(__("Create"));
		}
	},

	facility_applicable(frm) {
		// Mirrors the server-side clear in _enforce_facility_area_rules() —
		// purely for immediate feedback; the server re-clears these
		// regardless on save.
		if (frm.doc.facility_applicable !== "Yes") {
			frm.set_value("facility_area", 0);
			frm.set_value("facility_area_unit", "");
		}
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
			frm.fields_dict.auditors.set_value([]);
		} else {
			frm.set_value("external_auditor", "");
			frm.set_value("external_agency_contact_person", "");
			frm.set_value("external_agency_contact_number", "");
			frm.set_value("external_agency_contact_email", "");
			frm.set_value("proof_of_visit", "");
		}
	},

	checklist_template(frm) {
		toggle_checklist_items_add_row(frm);
		if (frm.doc.checklist_template) {
			frm.call("load_checklist_from_template").then(() => {
				frm.refresh_field("checklist_items");
			});
		} else {
			// Rows loaded from the just-removed template no longer mean
			// anything (their Item/Category came from that template, not
			// from hand entry) — cleared out rather than left sitting there
			// as stale, now-editable leftovers.
			frm.clear_table("checklist_items");
			frm.refresh_field("checklist_items");
		}
	},

	license_template(frm) {
		toggle_licenses_add_row(frm);
		if (frm.doc.license_template) {
			frm.call("load_licenses_from_template").then(() => frm.refresh_field("licenses"));
		} else {
			frm.clear_table("licenses");
			frm.refresh_field("licenses");
		}
	},

	insurance_template(frm) {
		toggle_insurance_certificates_add_row(frm);
		if (frm.doc.insurance_template) {
			frm.call("load_insurance_from_template").then(() => frm.refresh_field("insurance_certificates"));
		} else {
			frm.clear_table("insurance_certificates");
			frm.refresh_field("insurance_certificates");
		}
	},

	before_submit(frm) {
		// Block by default; only lift it once the server confirms this
		// document is actually ready. check_submit_readiness() runs the
		// same structural checks before_submit() itself enforces — so a
		// real blocking problem (a disabled template, missing evidence,
		// an unanswered item, etc.) surfaces here FIRST, before the
		// Failed-Result confirmation below — a "Yes, submit anyway" click
		// can't be immediately followed by an unrelated failure (same
		// pattern as Vendor Background Check).
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

			if (result.outcome !== "Failed") {
				frappe.validated = true;
				return;
			}

			// A hard block here would prevent ever recording a genuinely
			// failed audit, so this is a confirmation, not a block — the
			// reviewer explicitly decides.
			return new Promise((resolve) => {
				frappe.confirm(
					__("This Compliance Audit's Result is Failed. Do you still want to submit?"),
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

function toggle_checklist_items_add_row(frm) {
	// A template's checklist items are meant to be used as-is — once one
	// is picked, rows can no longer be added, removed, or duplicated by
	// hand, and Item/Category (which came straight from the template) can
	// no longer be hand-edited either; clearing the template unlocks the
	// grid again, including Item/Category, so rows can be entered by hand.
	//
	// Must go through set_df_property (not a direct grid.cannot_add_rows /
	// grid.cannot_delete_rows assignment) — the grid's own Delete and
	// Duplicate button visibility checks read these two flags off the
	// field's docfield object (this.df), not off the grid instance, so
	// setting them directly on the grid instance is silently ignored by
	// exactly those two buttons (same fix as Vendor Background Check's
	// compliance_checks table and Vendor Background Check Reference's
	// ratings table). Row-field read_only is read off the same docfield
	// object, so it needs the same treatment.
	const locked = !!frm.doc.checklist_template;
	frm.set_df_property("checklist_items", "cannot_add_rows", locked);
	frm.set_df_property("checklist_items", "cannot_delete_rows", locked);
	frm.fields_dict.checklist_items.grid.update_docfield_property("item", "read_only", locked);
	frm.fields_dict.checklist_items.grid.update_docfield_property("category", "read_only", locked);
	frm.fields_dict.checklist_items.grid.refresh();
}

function toggle_licenses_add_row(frm) {
	// Same reasoning and mechanism as toggle_checklist_items_add_row — but
	// only license_type itself is ever populated from the template (see
	// load_licenses_from_template), so only that column gets locked.
	// issuing_authority has no template-driven source at all; locking it
	// too would leave it permanently blank with no way to ever fill it in.
	const locked = !!frm.doc.license_template;
	frm.set_df_property("licenses", "cannot_add_rows", locked);
	frm.set_df_property("licenses", "cannot_delete_rows", locked);
	frm.fields_dict.licenses.grid.update_docfield_property("license_type", "read_only", locked);
	frm.fields_dict.licenses.grid.refresh();
}

function toggle_insurance_certificates_add_row(frm) {
	// Same reasoning as toggle_licenses_add_row above — only insurance_type
	// comes from the template (see load_insurance_from_template); insurer
	// always needs to be filled in by hand, template or not.
	const locked = !!frm.doc.insurance_template;
	frm.set_df_property("insurance_certificates", "cannot_add_rows", locked);
	frm.set_df_property("insurance_certificates", "cannot_delete_rows", locked);
	frm.fields_dict.insurance_certificates.grid.update_docfield_property("insurance_type", "read_only", locked);
	frm.fields_dict.insurance_certificates.grid.refresh();
}

// Registered under the CHILD doctype's own name, not the parent's — frm
// still refers to the parent form inside the handler (same pattern as e.g.
// Sales Order Item.add_schedule in sales_order.js).
frappe.ui.form.on("Vendor Compliance Audit License", {
	valid_globally(frm, cdt, cdn) {
		if (locals[cdt][cdn].valid_globally) {
			frappe.model.set_value(cdt, cdn, "valid_countries", "");
			frappe.model.set_value(cdt, cdn, "valid_states", "");
		}
	},
});

frappe.ui.form.on("Vendor Compliance Audit Insurance", {
	valid_globally(frm, cdt, cdn) {
		if (locals[cdt][cdn].valid_globally) {
			frappe.model.set_value(cdt, cdn, "valid_countries", "");
			frappe.model.set_value(cdt, cdn, "valid_states", "");
		}
	},
});
