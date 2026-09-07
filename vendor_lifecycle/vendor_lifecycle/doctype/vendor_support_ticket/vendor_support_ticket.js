// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Support Ticket", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		// Status is deliberately locked on the field itself (see
		// vendor_support_ticket.json) — every transition goes through one
		// of these whitelisted methods instead of a free-form Select, so
		// each one can enforce its own rule (e.g. can't Close before
		// Resolved) and send the right notification. Grouped under one
		// "Status" dropdown since these are all internal, staff-side moves.
		// Close Ticket is deliberately NOT here — only the vendor can close
		// a ticket, from the Supplier Portal (see close_ticket()), and once
		// closed it's final — no Desk-side undo.
		const STATUS_GROUP = __("Status");

		if (["Open", "Reopened"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Start Progress"),
				() => {
					frm.call("start_progress").then(() => frm.reload_doc());
				},
				STATUS_GROUP
			);
		}

		if (["Open", "In Progress", "Reopened"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Mark Resolved"),
				() => {
					frappe.prompt(
						[
							{
								fieldname: "resolution",
								fieldtype: "Text Editor",
								label: __("Resolution Notes"),
								reqd: 1,
								default: frm.doc.resolution,
							},
						],
						(values) => {
							frm.call("mark_resolved", { resolution: values.resolution }).then(() => frm.reload_doc());
						},
						__("Mark Resolved")
					);
				},
				STATUS_GROUP
			);

			frm.add_custom_button(
				__("Mark Invalid"),
				() => {
					frappe.prompt(
						[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason") }],
						(values) => {
							frm.call("mark_invalid", { reason: values.reason }).then(() => frm.reload_doc());
						},
						__("Mark Invalid")
					);
				},
				STATUS_GROUP
			);
		}

		if (["Resolved", "Invalid"].includes(frm.doc.status)) {
			frm.add_custom_button(
				__("Reopen"),
				() => {
					frm.call("reopen_ticket").then(() => frm.reload_doc());
				},
				STATUS_GROUP
			);
		}
	},
});
