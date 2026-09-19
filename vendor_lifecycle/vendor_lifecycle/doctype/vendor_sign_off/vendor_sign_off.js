// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Sign Off", {
	sign_off_failed(frm) {
		// refresh() only re-evaluates the "Send Email" button's visibility
		// on load/save — without this, checking the box wouldn't hide it
		// until the next reload.
		frm.refresh();
	},
	before_submit(frm) {
		// Block by default; only lift it once the server confirms this
		// document is actually ready — same pattern as every other stage
		// doctype's own before_submit.
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

			// Always confirm before submitting — same pattern as
			// Compliance Audit / Sampling Evaluation / Background Check:
			// the message makes the consequence explicit so the reviewer
			// isn't surprised by a side effect they didn't see coming. For
			// a Renewal, disabling/re-enabling the vendor isn't automatic
			// the way it is for Onboarding/Reboarding — it only happens if
			// the relevant Settings checkbox is on — so the message says
			// so instead of promising something that might not happen.
			let message;
			if (frm.doc.is_renewal) {
				message = result.sign_off_failed
					? __(
							"Mark this Renewal as Failed and submit? The vendor is only disabled if \"Disable Vendor"
								+ " on Contract Expiry/Failure\" is on in Vendor Lifecycle Settings."
					  )
					: __("Submit this Renewal?");
			} else {
				message = result.sign_off_failed
					? __("Mark this Sign-off as Failed and submit? The vendor will be disabled.")
					: __("Submit this Sign-off? The vendor will be activated.");
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
		// vendor is mandatory but only editable for a Renewal — for
		// Onboarding/Reboarding it's auto-derived and must arrive already
		// filled in, so every "Create" button that opens this doctype
		// (Vendor KYC's own dropdown, Vendor Reboarding Request's own
		// dropdown, Vendor Sampling Evaluation's own "next stage" button)
		// resolves vendor itself, synchronously, before opening this form —
		// no lookup needed here at all.

		// Same reasoning as Vendor Compliance Audit's own identical
		// refresh() check — Onboarding/Reboarding are only ever legitimate
		// when they arrive from one of those same "Create" buttons
		// (kyc/reboarding_request already set — signoff_type's own
		// read_only_depends_on then locks the field). Opened any other way
		// (the plain "+ New" button), the only thing left that ever makes
		// sense to pick by hand is Renewal.
		if (frm.is_new() && !frm.doc.kyc && !frm.doc.reboarding_request) {
			frm.set_df_property("signoff_type", "options", "\nRenewal");
		}

		// Offered directly on the failed record itself, not only from the
		// KYC's own "Create" dropdown — the same server-side exception
		// (_require_no_active_signoff_unless_failed) that allows a retry
		// only actually applies once this Sign-off is Submitted and
		// Failed, so the button only needs to appear then; if some other,
		// newer Sign-off already exists for this KYC (this one has since
		// been superseded), the server throws a clear error rather than
		// silently doing nothing. A Renewal has no such "retry in place"
		// concept at all (see _require_no_active_signoff_unless_failed's
		// own is_renewal branch, and stage_sequencing.force_override_
		// stage's identical exclusion) — no Create-style button of any
		// kind is offered on a Renewal/Ad-hoc document; the only way to
		// start another one is the plain "+ New" button.
		if (!frm.is_new() && frm.doc.docstatus === 1 && frm.doc.sign_off_failed && !frm.doc.is_renewal) {
			frm.add_custom_button(__("Retry Sign-off"), () => {
				// vendor is set explicitly, once the new form is confirmed
				// to exist, same as every other "Create" button in this
				// app — handing it over as a plain route_options value
				// alongside kyc/reboarding_request isn't reliable enough on
				// its own for a mandatory field.
				const vendor = frm.doc.vendor;
				const options = frm.doc.is_reboarding
					? { reboarding_request: frm.doc.reboarding_request, signoff_type: "Reboarding" }
					: { kyc: frm.doc.kyc, signoff_type: "Onboarding" };
				frappe.new_doc("Vendor Sign Off", options).then(() => {
					if (cur_frm && cur_frm.doctype === "Vendor Sign Off" && cur_frm.is_new()) {
						cur_frm.set_value("vendor", vendor);
					}
				});
			});
		}

		// Only offered once there's something to send to, only while this
		// is still a draft, and never once marked failed — matches
		// send_signoff_email()'s own server-side guards.
		if (!frm.is_new() && frm.doc.docstatus === 0 && frm.doc.supplier_email && !frm.doc.sign_off_failed) {
			frm.add_custom_button(__("Send Email"), () => {
				const recipients = [frm.doc.supplier_email, frm.doc.additional_email].filter(Boolean).join(", ");
				// Nothing server-side stops this being sent more than once —
				// the only real duplicate-send guard is this confirm text
				// itself calling out that it already went out, so a repeat
				// click (or coming back to it later) isn't silently treated
				// as the first send.
				const message = frm.doc.last_reminder_sent
					? __("This Sign-off email was already sent on {0} — send it again to {1}?", [
							frappe.datetime.str_to_user(frm.doc.last_reminder_sent),
							recipients,
					  ])
					: __("Send the Sign-off email to {0}?", [recipients]);
				frappe.confirm(message, () => {
					frm.call("send_signoff_email").then((r) => {
						if (r.message) {
							frappe.msgprint({
								title: __("Email Sent"),
								indicator: "green",
								message: __("Sent to {0}.", [r.message.sent_to.join(", ")]),
							});
						}
					});
				});
			});
		}

		// Only once the initial email has actually gone out at least once,
		// and something signed is still missing — matches
		// send_signoff_followup()'s own server-side guards.
		if (
			!frm.is_new()
			&& frm.doc.docstatus === 0
			&& !frm.doc.sign_off_failed
			&& frm.doc.last_reminder_sent
			&& (!frm.doc.signed_contract || (frm.doc.code_of_conduct_acknowledged && !frm.doc.code_of_conduct_document))
		) {
			frm.add_custom_button(__("Send Follow-up"), () => {
				const message =
					frm.doc.last_reminder_sent === frappe.datetime.get_today()
						? __("A Sign-off email was already sent today — send another follow-up anyway?")
						: __("Send a follow-up reminder about the still-missing signed document(s)?");
				frappe.confirm(message, () => frm.call("send_signoff_followup").then(() => frm.reload_doc()));
			});
		}
	},
});
