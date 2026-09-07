// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

const CHECKLIST_CONFIRM_STATUSES = ["Invalid", "Unable to Complete"];

frappe.ui.form.on("Vendor Deboarding Checklist", {
	refresh(frm) {
		// Fixed set of tasks resolved from the site's Default Checklist
		// Template — staff work through the rows, they don't add/remove
		// them.
		if (frm.fields_dict.checklist_items) {
			const grid = frm.fields_dict.checklist_items.grid;
			grid.cannot_add_rows = true;
			grid.df.cannot_add_rows = true;
			grid.df.cannot_delete_rows = true;
			grid.refresh();
		}

		// Real-time, not only-after-save: a brand new checklist gets its
		// Checklist Template and Items populated as soon as the form opens.
		if (frm.is_new() && !frm.doc.checklist_items.length) {
			frappe.call({
				method:
					"vendor_lifecycle.vendor_lifecycle.doctype.vendor_deboarding_checklist.vendor_deboarding_checklist.get_default_checklist",
				callback: (r) => {
					const result = r.message || {};
					if (result.checklist_template) frm.set_value("checklist_template", result.checklist_template);
					(result.items || []).forEach((row) => frm.add_child("checklist_items", row));
					frm.refresh_field("checklist_items");
				},
			});
		}

		if (frm.doc.docstatus === 0 && !frm.doc.signed_clearance_certificate && frm.doc.clearance_attachment) {
			frm.add_custom_button(__("Send Clearance Certificate"), () => {
				frappe.confirm(
					__("Email the clearance certificate to the Supplier for signing?"),
					() => frm.call("send_clearance_certificate_email").then(() => frm.reload_doc())
				);
			});
		}

		if (frm.doc.docstatus === 0 && !frm.doc.signed_clearance_certificate && frm.doc.last_reminder_sent) {
			frm.add_custom_button(__("Send Follow-up"), () => {
				frappe.confirm(
					__("Send a follow-up reminder to the Supplier about the signed clearance certificate?"),
					() => frm.call("send_clearance_certificate_followup").then(() => frm.reload_doc())
				);
			});
		}

		if (!frm.doc.deboarding_request || frm.is_new()) return;

		// A visible "still loading" line first, so an empty tab doesn't
		// read as "confirmed nothing outstanding" while the call is still
		// in flight.
		frm.set_df_property(
			"open_transactions_detail",
			"options",
			`<div style="text-align:center; padding:36px 16px; color:#8d99a6;">
				<div>${__("Loading open transactions…")}</div>
			</div>`
		);
		frm.refresh_field("open_transactions_detail");

		frm.call("get_open_transactions").then((r) => {
			const data = r.message || {};
			const pos = data.open_purchase_orders || [];
			const invoices = data.unpaid_purchase_invoices || [];
			const advances = data.advance_payments || [];

			if (!pos.length && !invoices.length && !advances.length) {
				frm.set_df_property(
					"open_transactions_detail",
					"options",
					`<div style="text-align:center; padding:36px 16px; color:#8d99a6;">
						<div style="font-size:32px; margin-bottom:8px;">✓</div>
						<div>${__("No open Purchase Orders, unpaid Purchase Invoices, or advance payments found.")}</div>
					</div>`
				);
				frm.refresh_field("open_transactions_detail");
				return;
			}

			const stat = (label, value, color) => `
				<div style="flex:1 1 0; min-width:150px; background:${color}; color:#fff; border-radius:10px;
					padding:16px; text-align:center; box-shadow:0 2px 6px rgba(0,0,0,0.08);">
					<div style="font-size:28px; font-weight:700; line-height:1;">${value}</div>
					<div style="font-size:11px; opacity:0.9; margin-top:6px; text-transform:uppercase; letter-spacing:0.5px;">
						${label}
					</div>
				</div>`;

			let html = `<div style="display:flex; gap:12px; flex-wrap:wrap; margin-bottom:22px;">`;
			html += stat(__("Open Purchase Orders"), pos.length, "#e74c3c");
			html += stat(__("Unpaid Purchase Invoices"), invoices.length, "#f39c12");
			html += stat(__("Advance Payments"), advances.length, "#2980b9");
			html += `</div>`;

			const table = (title, icon, color, columns, rowsHtml) => {
				if (!rowsHtml.length) return "";
				let t = `
					<div style="margin-bottom:20px; border:1px solid #e5e8eb; border-radius:10px; overflow:hidden;
						box-shadow:0 1px 3px rgba(0,0,0,0.05);">
						<div style="background:${color}; color:#fff; padding:10px 16px; font-weight:600; font-size:13px;">
							${icon} ${title}
						</div>
						<table style="width:100%; border-collapse:collapse; font-size:12px;">
							<thead><tr style="background:#f8f9fb;">
								${columns.map((c) => `<th style="text-align:left; padding:8px 16px; color:#6b7684;
									font-weight:600; border-bottom:1px solid #e5e8eb;">${c}</th>`).join("")}
							</tr></thead>
							<tbody>`;
				rowsHtml.forEach((row, i) => {
					t += `<tr style="background:${i % 2 === 0 ? "#ffffff" : "#fafbfc"};">${row}</tr>`;
				});
				t += `</tbody></table></div>`;
				return t;
			};

			html += table(
				__("Open Purchase Orders"), "📄", "#e74c3c",
				[__("Purchase Order"), __("Status"), __("Amount")],
				pos.map((po) => `
					<td style="padding:8px 16px;"><a href="/app/purchase-order/${po.name}">${po.name}</a></td>
					<td style="padding:8px 16px;">${po.status}</td>
					<td style="padding:8px 16px; text-align:right;">${format_currency(po.grand_total)}</td>
				`)
			);
			html += table(
				__("Unpaid Purchase Invoices"), "🧾", "#f39c12",
				[__("Purchase Invoice"), __("Outstanding")],
				invoices.map((pi) => `
					<td style="padding:8px 16px;"><a href="/app/purchase-invoice/${pi.name}">${pi.name}</a></td>
					<td style="padding:8px 16px; text-align:right;">${format_currency(pi.outstanding_amount)}</td>
				`)
			);
			html += table(
				__("Advance Payments"), "💳", "#2980b9",
				[__("Payment Entry"), __("Date"), __("Unallocated")],
				advances.map((pe) => `
					<td style="padding:8px 16px;"><a href="/app/payment-entry/${pe.name}">${pe.name}</a></td>
					<td style="padding:8px 16px;">${frappe.datetime.str_to_user(pe.posting_date)}</td>
					<td style="padding:8px 16px; text-align:right;">${format_currency(pe.unallocated_amount)}</td>
				`)
			);

			frm.set_df_property("open_transactions_detail", "options", html);
			frm.refresh_field("open_transactions_detail");
		});

		// Only offered once submitted — the server checks the same role
		// again inside temporarily_enable_supplier() itself, in case of a
		// stale page or a direct API call.
		if (frm.doc.docstatus === 1) {
			frm.call("get_temporary_enable_button_info").then((r) => {
				const info = r.message || {};
				if (!info.show) return;

				const label = info.is_temporarily_enabled
					? __("Temporarily Enabled (until {0})", [frappe.datetime.str_to_user(info.expires_on)])
					: __("Temporarily Enable Supplier");
				frm.add_custom_button(label, () => {
					frappe.confirm(
						__(
							"Temporarily enable this Supplier for 7 days? It will be automatically disabled again"
								+ " afterward unless this is repeated."
						),
						() => frm.call("temporarily_enable_supplier").then(() => frm.reload_doc())
					);
				});
			});
		}
	},

	before_submit(frm) {
		// Not Started / In Progress rows already hard-block submit
		// server-side (before_submit) — this is purely a "you sure?" for
		// the statuses that ARE allowed to submit but mean something
		// didn't go cleanly.
		const flagged = (frm.doc.checklist_items || []).filter((row) =>
			CHECKLIST_CONFIRM_STATUSES.includes(row.status)
		);
		if (!flagged.length) return;

		return new Promise((resolve, reject) => {
			frappe.confirm(
				__(
					"{0} checklist item(s) are marked Invalid or Unable to Complete. Submit this Checklist anyway?",
					[flagged.length]
				),
				resolve,
				reject
			);
		});
	},
});
