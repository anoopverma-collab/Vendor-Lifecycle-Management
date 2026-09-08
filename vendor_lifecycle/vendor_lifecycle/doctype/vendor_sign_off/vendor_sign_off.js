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
			// the message makes the consequence explicit (Failed disables
			// the vendor) so the reviewer isn't surprised by a side effect
			// they didn't see coming.
			const message = result.sign_off_failed
				? __("Mark this Sign-off as Failed and submit? The vendor will be disabled.")
				: __("Submit this Sign-off? The vendor will be activated.");

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
		// "Create Sign-off" (from Vendor KYC's "Create" dropdown, or the
		// per-stage button on Vendor Sampling Evaluation) opens a new
		// document via frappe.new_doc(doctype, {kyc: ...}) — that prefill
		// mechanism (route_options) sets the field with a raw property
		// assignment, not frm.set_value(), so it never fires a "kyc
		// changed" trigger and vendor (server-side auto-synced from the
		// KYC's Supplier via sync_vendor_field, but only ever computed
		// when the document is actually saved) is still blank the moment
		// the form first renders. Since vendor is both mandatory and
		// read-only, Frappe's own client-side "fill in mandatory fields"
		// check would otherwise block the very first save attempt before
		// the server ever gets a chance to fill it in — an unbreakable
		// dead end, since the user has no way to type into a read-only
		// field themselves. refresh() always fires regardless of how kyc
		// got its value, so this fills vendor in immediately, before any
		// save is attempted. Same pattern as every other stage doctype.
		if (frm.is_new() && frm.doc.kyc && !frm.doc.vendor) {
			frappe.db.get_value("Vendor KYC", frm.doc.kyc, "supplier").then((r) => {
				if (r.message && r.message.supplier) {
					frm.set_value("vendor", r.message.supplier);
				}
			});
		}

		// Offered directly on the failed record itself, not only from the
		// KYC's own "Create" dropdown — the same server-side exception
		// (_require_no_active_signoff_unless_failed) that allows a retry
		// only actually applies once this Sign-off is Submitted and
		// Failed, so the button only needs to appear then; if some other,
		// newer Sign-off already exists for this KYC (this one has since
		// been superseded), the server throws a clear error rather than
		// silently doing nothing.
		if (!frm.is_new() && frm.doc.docstatus === 1 && frm.doc.sign_off_failed) {
			frm.add_custom_button(__("Retry Sign-off"), () => {
				frappe.new_doc("Vendor Sign Off", { kyc: frm.doc.kyc });
			});
		}

		// Only offered once there's something to send to, only while this
		// is still a draft, and never once marked failed — matches
		// send_signoff_email()'s own server-side guards.
		if (!frm.is_new() && frm.doc.docstatus === 0 && frm.doc.supplier_email && !frm.doc.sign_off_failed) {
			frm.add_custom_button(__("Send Email"), () => {
				const recipients = [frm.doc.supplier_email, frm.doc.additional_email].filter(Boolean).join(", ");
				frappe.confirm(
					__("Send the Sign-off email to {0}?", [recipients]),
					() => {
						frm.call("send_signoff_email").then((r) => {
							if (r.message) {
								frappe.msgprint({
									title: __("Email Sent"),
									indicator: "green",
									message: __("Sent to {0}.", [r.message.sent_to.join(", ")]),
								});
							}
						});
					}
				);
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
				frappe.confirm(
					__("Send a follow-up reminder about the still-missing signed document(s)?"),
					() => frm.call("send_signoff_followup").then(() => frm.reload_doc())
				);
			});
		}
	},
});
