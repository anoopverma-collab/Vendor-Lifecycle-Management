// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Reboarding Request", {
	refresh(frm) {
		// Same reasoning as Vendor KYC's own identical block — exactly one
		// way to create each of these (the "Create" dropdown below), not a
		// second competing one via the Connections tab's own "+" shortcut,
		// which would appear automatically now that these 4 doctypes have
		// their own reboarding_request Link field.
		frm.can_make_methods = frm.can_make_methods || {};
		["Vendor Background Check", "Vendor Compliance Audit", "Vendor Sampling Evaluation", "Vendor Sign Off"].forEach(
			(doctype) => {
				frm.can_make_methods[doctype] = () => false;
			}
		);
	},
});

frappe.ui.form.on("Vendor Reboarding Request", {
	setup(frm) {
		// A plain link_filters JSON entry can't express this: Frappe's own
		// search_widget auto-excludes disabled=1 records from every Link
		// field by default (any doctype with a "disabled" Check field),
		// unless include_disabled is explicitly passed inside the filters
		// dict — confirmed directly in frappe/desk/search.py. Vendor
		// Deboarding Request's own vendor field (filtered to disabled=0)
		// never hit this, since that direction already matches Frappe's
		// default; this one needs the opposite, so it needs a real
		// get_query with include_disabled, not link_filters.
		//
		// disabled=1 alone isn't enough either — a Supplier can be
		// disabled mid-onboarding (a failed Background Check/Compliance
		// Audit/Sampling Evaluation also disables it) with no relation to
		// ever having been deboarded. vendor_lifecycle_status "Disabled"
		// is only ever set by Vendor Deboarding Checklist's own submit —
		// confirmed against live data, where several currently-disabled
		// Suppliers had no such status at all.
		frm.set_query("vendor", () => ({
			filters: { disabled: 1, vendor_lifecycle_status: "Disabled", include_disabled: 1 },
		}));
	},
	refresh(frm) {
		add_reject_button(frm);
		add_create_buttons(frm);

		// Always clear first, rather than leaving whatever was last
		// rendered on screen - an HTML field's content isn't tied to a
		// real stored value, so it doesn't reset itself just because the
		// form navigated to a different (or a brand new) document.
		frm.set_df_property("financial_summary", "options", "");

		if (!frm.doc.vendor || frm.is_new()) return;

		// Capture which document this call is actually for - if the form
		// has since navigated elsewhere by the time this resolves (the
		// same frm instance is reused across route changes), the response
		// is stale and must not be stamped onto whatever's showing now.
		const requestedFor = frm.doc.name;
		frm.call("get_financial_summary").then((r) => {
			if (frm.doc.name !== requestedFor) return;

			const data = r.message || {};
			const fmt = (v) => format_currency(v || 0, data.currency);
			const cards = [
				{ label: __("Total Invoices"), value: fmt(data.invoices_total), color: "#2980b9" },
				{ label: __("Total Debit Notes"), value: fmt(data.debit_notes_total), color: "#f39c12" },
				{ label: __("Net Invoices / Trade Amount"), value: fmt(data.net_total), color: "#27ae60" },
			];

			let html = `<div style="display:flex; gap:12px; flex-wrap:wrap; align-items:stretch;">`;
			cards.forEach((card) => {
				html += `
					<div style="flex:1 1 0; min-width:180px; border-radius:8px; padding:14px; background:${card.color}; color:#fff; display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center;">
						<div style="font-size:22px; font-weight:700;">${card.value}</div>
						<div style="font-size:12px; opacity:0.9;">${card.label}</div>
					</div>`;
			});
			html += `</div>
				<div class="text-muted" style="margin-top:8px;">
					${__("Lifetime totals with this vendor — Net = Total Invoices minus Total Debit Notes.")}
				</div>`;

			frm.set_df_property("financial_summary", "options", html);
			frm.refresh_field("financial_summary");
		});
	},
});

frappe.ui.form.on("Vendor Reboarding Request", {
	refresh(frm) {
		// Same shared style tag Vendor Onboarding Request's own pipeline
		// progress widget injects — reused by id, so it's only ever added
		// to the page once regardless of which of the two forms loads
		// first. Frappe's bundled CSS only styles progress-bar-success/
		// -warning/-info/-danger — there's no built-in black variant.
		if (!document.getElementById("vendor-lifecycle-pipeline-progress-style")) {
			$("<style>", {
				id: "vendor-lifecycle-pipeline-progress-style",
				html: ".progress-bar-not-started { background-color: #000 !important; }",
			}).appendTo("head");
		}

		const PROGRESS_CLASS = {
			"Completed": "progress-bar-success",
			"In Progress": "progress-bar-warning",
			"Not Started": "progress-bar-not-started",
			"Skipped": "progress-bar-danger",
			"Failed": "progress-bar-danger",
			// Distinct from a clean "Completed" (green) and a genuine
			// "Failed" (red) — this stage's own outcome is still really
			// Failed/Rejected underneath (force_override_stage() never
			// changes that field), a manager just lifted the block, so it
			// reads as "passed, but via an exception" rather than blending
			// into either. Same palette as Vendor Onboarding Request's own
			// identical widget.
			"Forcefully Passed": "progress-bar-info",
		};

		function render_pipeline_progress(stages) {
			stages.forEach((s) => {
				frm.dashboard.add_progress(
					s.label,
					[{ title: s.state, width: "100%", progress_class: PROGRESS_CLASS[s.state] || "progress-bar-not-started" }],
					`${s.label}: ${s.state}`
				);
			});
		}

		if (frm.doc.__islocal || frm.doc.docstatus !== 1) {
			return;
		}

		frm.call("get_pipeline_progress").then((r) => {
			const stages = r.message || [];
			if (stages.length) {
				render_pipeline_progress(stages);
			}
		});
	},
});

function add_reject_button(frm) {
	// The Workflow (once active) generates its own Send for Approval/
	// Approve/Reject/Trash/Cancel buttons from its Transition rows - this
	// manual button is only the fallback for a site with no Workflow
	// configured for this doctype, same as Vendor KYC's own. Only offered
	// on a saved, still-In Progress, not-already-rejected record.
	if (
		frm.doc.__islocal ||
		frm.doc.docstatus !== 0 ||
		frm.doc.status === "Rejected" ||
		frappe.model.has_workflow(frm.doctype)
	) {
		return;
	}

	frm.add_custom_button(__("Reject"), () => {
		frappe.confirm(
			__("Reject this Vendor Reboarding Request? Once rejected, it can no longer be edited or submitted."),
			() => {
				frm.call("reject").then(() => frm.reload_doc());
			}
		);
	});
}

function add_create_buttons(frm) {
	// Only once this Request is actually Approved (submitted) — before
	// that there's no re-boarding pipeline to redo checks against yet.
	if (frm.doc.docstatus !== 1 || frm.doc.status !== "Approved") {
		return;
	}

	// Same mechanism as Vendor KYC's own "Create" dropdown — an unsaved,
	// client-side draft (frappe.new_doc), nothing written to the database
	// until the user actually clicks Save. reboarding_request (and the
	// Type field, for the 3 doctypes that have one) are handed over as
	// route_options; vendor (and, for Background Check, is_reboarding) are
	// then set explicitly via cur_frm.set_value() once frappe.new_doc's
	// own promise confirms the new form actually exists — see open_new()
	// below. A plain route_options value alone isn't reliable enough for
	// a mandatory field; confirmed the hard way on this exact button.
	const stage_labels = {
		"Vendor Background Check": __("Background Check"),
		"Vendor Compliance Audit": __("Compliance Audit"),
		"Vendor Sampling Evaluation": __("Sampling Evaluation"),
		"Vendor Sign Off": __("Sign-off"),
	};
	// Same map as Vendor KYC's own "Create" dropdown — doctypes with their
	// own Onboarding/Reboarding/Renewal Type field.
	const stage_type_fields = {
		"Vendor Compliance Audit": "audit_type",
		"Vendor Sampling Evaluation": "sampling_type",
		"Vendor Sign Off": "signoff_type",
	};

	// "Stop and ask" — if a mandatory earlier stage for this re-boarding
	// request hasn't actually passed (or been force-overridden) yet,
	// don't open the new form at all; tell the user what to complete
	// first (see get_reboarding_stage_info's own missing_requirement,
	// backed by the exact same check stage_sequencing.
	// enforce_sequential_creation would otherwise only catch once they
	// tried to save). Re-boarding still doesn't enforce a FIXED order —
	// this only fires for a stage that's genuinely configured mandatory.
	const open_new = (doctype, missing_requirement) => {
		const missing = missing_requirement[doctype];
		if (missing) {
			frappe.msgprint({
				title: __("Complete {0} First", [stage_labels[missing]]),
				indicator: "orange",
				message: __(
					"{0} is required before starting {1} for this re-boarding request — complete (or force-override) it first.",
					[stage_labels[missing], stage_labels[doctype]]
				),
			});
			return;
		}
		const type_field = stage_type_fields[doctype];
		const vendor = frm.doc.vendor;
		// See Vendor KYC's own identical "Create" dropdown for why vendor
		// is set this way (an explicit set_value() once the new form is
		// confirmed to exist) instead of just handing it over as a
		// route_options value alongside reboarding_request — the latter
		// alone isn't reliable enough for a mandatory field; confirmed via
		// a real "Vendor is required" block on the freshly-opened form.
		frappe.new_doc(doctype, {
			reboarding_request: frm.doc.name,
			...(type_field ? { [type_field]: "Reboarding" } : {}),
		}).then(() => {
			if (cur_frm && cur_frm.doctype === doctype && cur_frm.is_new()) {
				cur_frm.set_value("vendor", vendor);
				// A doctype with its own Type field (audit_type,
				// sampling_type, signoff_type) derives is_reboarding from
				// that automatically, server-side — but Vendor Background
				// Check has no such field yet (no Renewal-equivalent Type
				// built for it), so it still needs is_reboarding set
				// explicitly here, same as kyc/vendor themselves.
				if (!type_field) {
					cur_frm.set_value("is_reboarding", 1);
				}
			}
		});
	};

	// Unlike Vendor KYC's own "Create" dropdown (which only offers
	// whichever stage get_available_stages() says is next, in strict
	// order), re-boarding doesn't enforce a FIXED order — see
	// stage_sequencing.enforce_sequential_creation's is_reboarding branch
	// — but whichever stage IS configured mandatory still gates whatever
	// normally comes after it (open_new's own missing_requirement check
	// above). Background Check / Compliance Audit / Sampling Evaluation
	// are always offered as buttons; only Sign-off has its own "already
	// exists"/"retry after Failed" shape to respect on top of that (see
	// get_reboarding_stage_info, mirroring Vendor Sign Off's own
	// _require_no_active_signoff_unless_failed). All 4 stop being offered
	// together, though, once reboarding_complete comes back true — a
	// re-boarding run with an actually-passed Sign Off is done; nothing
	// else should look creatable (the server enforces this same rule too,
	// via stage_sequencing.block_if_reboarding_completed, not just this
	// button's own visibility).
	frm.call("get_reboarding_stage_info").then((r) => {
		const info = r.message || {};

		// Purely informational (Sign-off is the last stage, nothing "next"
		// to block) — sign_off_is_retry already means exactly "the latest
		// submitted re-boarding Sign Off is Failed, and hasn't been
		// retried yet", same signal Vendor KYC's own identical banner
		// uses for onboarding.
		if (info.sign_off_is_retry) {
			frm.dashboard.clear_headline();
			frm.set_intro(
				__("This re-boarding Sign-off has Failed — the Supplier remains disabled. Use \"Retry Sign-off\" below once ready."),
				"red"
			);
		}

		if (info.reboarding_complete) {
			return;
		}
		const missing_requirement = info.missing_requirement || {};
		frm.add_custom_button(stage_labels["Vendor Background Check"], () => {
			open_new("Vendor Background Check", missing_requirement);
		}, __("Create"));
		frm.add_custom_button(stage_labels["Vendor Compliance Audit"], () => {
			open_new("Vendor Compliance Audit", missing_requirement);
		}, __("Create"));
		frm.add_custom_button(stage_labels["Vendor Sampling Evaluation"], () => {
			open_new("Vendor Sampling Evaluation", missing_requirement);
		}, __("Create"));
		if (info.sign_off_available) {
			frm.add_custom_button(info.sign_off_is_retry ? __("Retry Sign-off") : __("Sign-off"), () => {
				open_new("Vendor Sign Off", missing_requirement);
			}, __("Create"));
		}
		frm.page.set_inner_btn_group_as_primary(__("Create"));
	});
}
