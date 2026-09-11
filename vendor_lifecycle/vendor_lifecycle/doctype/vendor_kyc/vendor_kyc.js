// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		// Every one of these already has its own guided way to be created
		// from this KYC (the "Create" dropdown, the "Tools" buttons, or
		// on_submit's automatic pass) — the Connections tab's own "+"
		// shortcut is hidden so there's exactly one way to create each,
		// not two competing ones.
		frm.can_make_methods = frm.can_make_methods || {};
		[
			"Vendor Background Check",
			"Vendor Compliance Audit",
			"Vendor Sampling Evaluation",
			"Vendor Sign Off",
			"Address",
			"Contact",
			"Bank Account",
			"Supplier",
			"Purchase Invoice",
		].forEach((doctype) => {
			frm.can_make_methods[doctype] = () => false;
		});
	},
});

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		if (frm.doc.status === "Rejected") {
			// Visible line so it's obvious just from looking at the form —
			// not only discoverable by trying to edit/submit and hitting an
			// error. Belt-and-suspenders: before_submit() on the server
			// already blocks submitting a rejected KYC outright; clearing
			// the primary action just removes the button so nobody sees an
			// option that would only error out. If any of this ever fails
			// to run (caching, a stale page, etc.), that server-side check
			// is still there as the real safety net.
			// Core Frappe's own "Submit this document to confirm" banner (for
			// any saved, still-Draft, submittable doctype) shares this exact
			// same message slot — clear it FIRST, then set ours, so ours is
			// guaranteed to be what's left showing rather than the other way
			// around (set_intro and clear_headline both write to the same
			// spot; whichever runs last wins).
			frm.dashboard.clear_headline();
			frm.set_intro(
				__("This Vendor KYC has been rejected and can no longer be edited or submitted."),
				"red"
			);
			frm.page.clear_primary_action();
		}

		// Manual reject path only makes sense when there's no Frappe
		// Workflow configured on this doctype — a workflow would bring its
		// own state-transition buttons, and this shouldn't compete with
		// those. Only offered on a saved, still-Draft, not-already-rejected
		// record — rejecting submits nothing; the doc stays at docstatus 0
		// forever once rejected, and reject() itself blocks a second click.
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
				__("Reject this Vendor KYC? Once rejected, it can no longer be edited or submitted."),
				() => {
					frm.call("reject").then(() => frm.reload_doc());
				}
			);
		});
	},
});

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		if (frm.doc.__islocal || !frm.doc.supplier) {
			return;
		}

		// Hides itself if the user doesn't have the configured role — and
		// the server checks the same role again inside toggle_supplier_freeze()
		// itself, in case of a stale page/cached button or a direct API call.
		frm.call("get_supplier_freeze_button_info").then((r) => {
			const info = r.message || {};
			if (!info.show) {
				return;
			}

			const label = info.is_frozen ? __("Unfreeze Supplier") : __("Freeze Supplier");
			frm.add_custom_button(label, () => {
				frappe.confirm(
					info.is_frozen
						? __("Unfreeze this Supplier? It will be usable in transactions again immediately.")
						: __("Freeze this Supplier? No transactions can be created against it until it's unfrozen."),
					() => {
						frm.call("toggle_supplier_freeze").then(() => frm.refresh());
					}
				);
			});
		});
	},
});

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		const gstin_section_fields = [
			"section_break_gstin_address",
			"gstin_address_line",
			"gstin_city",
			"gstin_state",
			"gstin_pincode",
			"is_address_same_as_gstin",
		];

		if (!(frm.doc.country === "India" && frm.doc.gstin_uin)) {
			return;
		}

		// Whether Fetch can actually work right now (India Compliance
		// installed AND its GST API enabled) — hides the button and the
		// whole fields section entirely rather than let it fail only after
		// being clicked.
		frm.call("can_fetch_gstin_details").then((r) => {
			if (!r.message) {
				frm.toggle_display(gstin_section_fields, false);
				return;
			}

			frm.add_custom_button(__("Fetch Address from GSTIN"), () => {
				frm.call("fetch_gstin_details").then((res) => {
					frm.refresh_field("gstin_address_line");
					frm.refresh_field("gstin_city");
					frm.refresh_field("gstin_state");
					frm.refresh_field("gstin_pincode");
					if (res.message) {
						frappe.show_alert(
							{
								message: __("Fetched from GSTIN — review the Address Verification section below."),
								indicator: "green",
							},
							7
						);
					}
				});
			});

			if (frm.doc.gstin_address_line || frm.doc.gstin_city) {
				frm.add_custom_button(__("Address Same as GSTIN"), () => {
					frappe.confirm(__("Copy the GSTIN-registered address into the Address fields above?"), () => {
						frm.set_value("address_line_1", frm.doc.gstin_address_line);
						frm.set_value("address_line_2", "");
						frm.set_value("city", frm.doc.gstin_city);
						frm.set_value("state", frm.doc.gstin_state);
						frm.set_value("pincode", frm.doc.gstin_pincode);
						frm.set_value("country", "India");
					});
				});
			}
		});
	},
});

frappe.ui.form.on("Vendor KYC", {
	// Real-time only — fires as soon as GSTIN is typed/changed on the form.
	// PAN stays a normal, editable field (not read-only): if someone types
	// something else into it afterward, or this GSTIN was pre-filled by a
	// flow that never fires this change event (e.g. "Start KYC"), the
	// server-side validate() unconditionally re-derives PAN from GSTIN on
	// every save regardless — so the saved value is always correct even if
	// this client-side preview didn't get a chance to run.
	gstin_uin(frm) {
		const gstin = (frm.doc.gstin_uin || "").trim().toUpperCase();
		if (gstin.length >= 12) {
			frm.set_value("pan_card", gstin.substring(2, 12));
		}
	},
});

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		frm.toggle_reqd("onboarding_request", !!frappe.boot.vendor_lifecycle_settings?.require_onboarding_request_for_kyc);
	},
});

frappe.ui.form.on("Vendor KYC", {
	refresh(frm) {
		// Address, Contact, and Bank Account are already created once,
		// automatically, when the Supplier is first created from this KYC —
		// these three buttons are only for adding an *additional* one of
		// each, so they only make sense once a Supplier actually exists.
		if (frm.doc.docstatus !== 1 || !frm.doc.supplier) {
			return;
		}

		frm.add_custom_button(__("Create Address"), () => {
			// India Compliance's own Address popup adds visible "Link
			// Document Type" / "Link Name" fields, and has its own working
			// auto-guess logic for them — but it only fires when opened
			// from a doctype on a fixed whitelist (Customer, Supplier,
			// Company, Lead, sales/purchase docs), which doesn't include
			// Vendor KYC. Rather than racing against that popup's own
			// internal timing from the outside (tried, unreliable), this
			// makes it use its own already-correct logic instead by
			// getting Vendor KYC onto that recognized list first.
			frappe.boot.sales_doctypes = frappe.boot.sales_doctypes || [];
			if (!frappe.boot.sales_doctypes.includes("Vendor KYC")) {
				frappe.boot.sales_doctypes.push("Vendor KYC");
			}
			frappe.dynamic_link = { doctype: "Supplier", doc: frm.doc, fieldname: "supplier" };

			frappe.ui.form.make_quick_entry("Address", (doc) => {
				// Hidden traceability field — set regardless of how the
				// Link Document Type/Name below turned out, since this
				// records which KYC the button was clicked from, not who
				// the address is linked to.
				frappe.db.set_value("Address", doc.name, "vendor_kyc", frm.doc.name);

				// If the reviewer changed Link Document Type/Name away
				// from what was pre-filled, respect that — don't silently
				// override their choice. Only fall back to linking this
				// KYC's Supplier when both ended up blank (e.g. they
				// cleared them out on purpose).
				if (doc.links && doc.links.length) {
					frappe.show_alert({ message: __("Address {0} created.", [doc.name]), indicator: "green" }, 5);
					return;
				}
				frappe.db
					.set_value("Address", doc.name, "links", [
						{ link_doctype: "Supplier", link_name: frm.doc.supplier },
					])
					.then(() => {
						frappe.show_alert({ message: __("Address {0} linked to Supplier.", [doc.name]), indicator: "green" }, 5);
					});
			});
		}, __("Tools"));

		// Contact always opens a full page in this Frappe version (it has
		// no quick-entry fields configured), and its own core script
		// already picks up this dynamic link correctly on a brand-new,
		// unsaved record — same mechanism Supplier's own "New Contact"
		// uses, just pointed at frm.doc.supplier instead of the KYC itself.
		frm.add_custom_button(__("Create Contact"), () => {
			frappe.dynamic_link = { doctype: "Supplier", doc: frm.doc, fieldname: "supplier" };
			frappe.new_doc("Contact", { vendor_kyc: frm.doc.name });
		}, __("Tools"));

		// Bank Account has no Dynamic Link child table — it links to its
		// party directly via party_type/party fields — so it's pre-filled
		// as route options instead, then always opens as a full page since
		// Bank Account has no quick-entry fields configured.
		frm.add_custom_button(__("Create Bank Account"), () => {
			frappe.new_doc("Bank Account", { party_type: "Supplier", party: frm.doc.supplier, vendor_kyc: frm.doc.name });
		}, __("Tools"));

		// "Create" dropdown: Purchase Invoice always shows; the 4 pipeline
		// stages only show when get_available_stages() (server-side, same
		// rule enforce_sequential_creation() enforces) says they're
		// actually creatable right now — so nothing offered here would
		// fail if clicked.
		const stage_labels = {
			"Vendor Background Check": "Background Check",
			"Vendor Compliance Audit": "Compliance Audit",
			"Vendor Sampling Evaluation": "Sampling Evaluation",
			"Vendor Sign Off": "Sign-off",
		};
		frm.call("get_available_stages").then((r) => {
			const result = r.message || {};
			if (result.stopped) {
				frm.dashboard.set_headline_alert(
					`<div>${__("This vendor's Onboarding Request has been stopped — see its Comments for why — re-open it to resume the pipeline.")}</div>`,
					"red"
				);
			}
			for (const doctype of result.stages || []) {
				// Sign-off is the only stage that can be offered a second
				// time (after a genuine Failed outcome, without cancelling
				// it first) — label it distinctly so it's clear this is a
				// retry, not a first attempt.
				const label =
					doctype === "Vendor Sign Off" && result.sign_off_is_retry
						? __("Retry Sign-off")
						: __(stage_labels[doctype]);
				frm.add_custom_button(label, () => {
					frappe.new_doc(doctype, { kyc: frm.doc.name });
				}, __("Create"));
			}
			// Purchase Invoice creation only ever happens via "Bill to
			// External Agency" — no generic option, since a KYC on its own
			// isn't something the vendor gets billed for.
			if (frm.doc.verified_by_external_agency && frm.doc.external_verification_agency) {
				frm.add_custom_button(__("Bill to External Agency"), () => {
					frappe.new_doc("Purchase Invoice", {
						supplier: frm.doc.external_verification_agency,
						vendor_lifecycle_source_doctype: frm.doc.doctype,
						vendor_lifecycle_source_name: frm.doc.name,
					});
				}, __("Create"));
			}

			// Same primary-blue styling as standard ERPNext's own "Create"
			// dropdown groups (e.g. Sales Invoice's "Create" button).
			frm.page.set_inner_btn_group_as_primary(__("Create"));

			if (result.background_check_failed) {
				frm.dashboard.clear_headline();
				frm.set_intro(
					__(
						"This vendor's Background Check has Failed — next onboarding stages cannot proceed," +
							" and the Supplier record has been disabled."
					),
					"red"
				);
			}

			if (result.compliance_audit_failed) {
				frm.dashboard.clear_headline();
				frm.set_intro(
					__(
						"This vendor's Compliance Audit has Failed — next onboarding stages cannot proceed," +
							" and the Supplier record has been disabled."
					),
					"red"
				);
			}

			if (result.sampling_evaluation_rejected) {
				frm.dashboard.clear_headline();
				frm.set_intro(
					__(
						"This vendor's Sampling Evaluation was Rejected — next onboarding stages cannot" +
							" proceed, and the Supplier record has been disabled."
					),
					"red"
				);
			}
		});
	},
});

frappe.ui.form.on("Vendor KYC", {
	verified_by_external_agency(frm) {
		// Only one of the two ever applies at a time — switching clears
		// whichever side just became irrelevant, rather than leaving a
		// stale value sitting in a now-hidden field.
		if (frm.doc.verified_by_external_agency) {
			// Table MultiSelect keeps a separate internal cache of
			// already-picked values (used to exclude them from its own
			// dropdown) that only the control's own set_value() resets -
			// neither frm.clear_table() nor frm.set_value() (the form-level
			// API) touch it, so a user removed either of those ways stays
			// invisible in the suggestions for the rest of the session even
			// though the field itself is empty again.
			frm.fields_dict.verified_by.set_value([]);
		} else {
			frm.set_value("external_verification_agency", "");
			frm.set_value("external_agency_contact_person", "");
			frm.set_value("external_agency_contact_number", "");
			frm.set_value("external_agency_contact_email", "");
			frm.set_value("proof_of_visit", "");
		}
	},
});

frappe.ui.form.on("Vendor KYC", {
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

frappe.ui.form.on("Vendor KYC", {
	// A convenience default only — years_in_business stays a normal,
	// editable field, so it can still be corrected by hand afterward.
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
