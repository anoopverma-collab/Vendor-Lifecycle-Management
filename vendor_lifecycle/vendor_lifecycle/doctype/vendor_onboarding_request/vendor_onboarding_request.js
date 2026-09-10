// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Onboarding Request", {
	refresh(frm) {
		// Vendor KYC is created via the "Start KYC" button (pre-filled
		// from this request); the 4 stage doctypes are created from the
		// KYC itself. The Connections tab's own "+" shortcut is hidden so
		// there's exactly one way to create each, not two competing ones.
		frm.can_make_methods = frm.can_make_methods || {};
		[
			"Vendor KYC",
			"Vendor Background Check",
			"Vendor Compliance Audit",
			"Vendor Sampling Evaluation",
			"Vendor Sign Off",
		].forEach((doctype) => {
			frm.can_make_methods[doctype] = () => false;
		});
	},
});

frappe.ui.form.on("Vendor Onboarding Request", {
	// A convenience default only — years_in_business stays a normal,
	// editable field, so whoever fills this in can still correct it by
	// hand afterward (e.g. the company changed legal entity but the
	// business itself is actually older).
	establishment_date(frm) {
		if (!frm.doc.establishment_date) return;
		const days = frappe.datetime.get_day_diff(frappe.datetime.now_date(), frm.doc.establishment_date);
		if (days < 0) {
			frappe.msgprint(__("Establishment Date cannot be in the future."));
			frm.set_value("establishment_date", "");
			return;
		}
		const years = days / 365.25;
		frm.set_value("years_in_business", Math.max(0, Math.round(years * 100) / 100));
	},
});

frappe.ui.form.on("Vendor Onboarding Request", {
	refresh(frm) {
		// Frappe's bundled CSS only styles progress-bar-success/-warning/
		// -info/-danger — there's no built-in black variant, and Desk forms
		// (unlike Web Forms) have no custom_css field to add one via JSON,
		// so it's injected once here instead.
		if (!document.getElementById("vendor-lifecycle-pipeline-progress-style")) {
			$("<style>", {
				id: "vendor-lifecycle-pipeline-progress-style",
				html: ".progress-bar-not-started { background-color: #000 !important; }",
			}).appendTo("head");
		}

		// Frappe's add_progress() only ever shows the "title" as an invisible
		// HTML tooltip attribute — the *only* part that renders as visible
		// text is "message". So the stage name has to live in the message
		// itself, or there's no way to tell which bar belongs to which stage.
		const PROGRESS_CLASS = {
			"Completed": "progress-bar-success",
			"In Progress": "progress-bar-warning",
			"Not Started": "progress-bar-not-started",
			"Skipped": "progress-bar-danger",
			"Rejected": "progress-bar-danger",
			"Failed": "progress-bar-danger",
			// Distinct from a clean "Completed" (green) and a genuine "Failed"
			// (red) — this stage's own outcome is still really Failed/Rejected
			// underneath (force_override_stage() never changes that field), a
			// manager just lifted the block, so it reads as "passed, but via
			// an exception" rather than blending into either.
			"Forcefully Passed": "progress-bar-info",
		};

		function render_pipeline_progress(stages) {
			// One individual, full-width bar per stage doctype — color
			// alone (not fill width) carries the state, since these are
			// discrete stages, not a continuous 0-100% quantity.
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

			const kyc_stage = stages.find((s) => s.label === "KYC");
			// A Rejected KYC is a dead attempt — same as "Not Started" for
			// the purposes of this button — and never blocks a fresh one,
			// regardless of the Duplicate KYC Handling setting below. That
			// setting only matters for a genuine duplicate: an earlier KYC
			// still Draft/In Progress/Approved. Matches Vendor KYC's own
			// duplicate-check.
			const has_kyc = !!kyc_stage && kyc_stage.state !== "Not Started" && kyc_stage.state !== "Rejected";

			const handling = frappe.boot.vendor_lifecycle_settings?.duplicate_kyc_handling || "Stop";
			if (has_kyc && handling === "Stop") {
				return;
			}

			frm.add_custom_button(__("Start KYC"), () => {
				frm.call("start_kyc").then((res) => {
					if (res.message) {
						// Opens a fresh, unsaved Vendor KYC form pre-filled with
						// this data — nothing is created until the reviewer saves
						// it themselves.
						frappe.new_doc("Vendor KYC", res.message);
					}
				});
			});
			// Standalone (ungrouped) button — same primary-blue styling as
			// standard ERPNext's own "+ Add"/primary-action buttons.
			frm.change_custom_button_type(__("Start KYC"), null, "primary");
		});
	},
});

frappe.ui.form.on("Vendor Onboarding Request", {
	setup(frm) {
		frm.set_query("state_master", () => ({
			filters: { country: frm.doc.country, disabled: 0 },
		}));
	},
	country(frm) {
		// A stale State from before switching Country can never linger —
		// state (the plain-text value everything else relies on) follows
		// via fetch_from once a new one is picked.
		if (frm.doc.state_master) {
			frm.set_value("state_master", "");
		}
		if (!frm.doc.country) {
			return;
		}
		frappe.db.count("State", { filters: { country: frm.doc.country, disabled: 0 } }).then((count) => {
			if (count) {
				return;
			}
			frappe.msgprint({
				title: __("No States Found"),
				indicator: "orange",
				message: __("No states are on file yet for {0}.", [frappe.bold(frm.doc.country)]),
				primary_action: {
					label: __("Create State"),
					action() {
						frappe.hide_msgprint();
						frappe.new_doc("State", { country: frm.doc.country });
					},
				},
			});
		});
	},
});
