// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.ui.form.on("Vendor Deboarding Request", {
	refresh(frm) {
		if (!frm.doc.vendor || frm.is_new()) return;

		frm.call("get_open_transactions").then((r) => {
			const data = r.message || {};
			const pos = data.open_purchase_orders || [];
			const invoices = data.unpaid_purchase_invoices || [];

			let html = `<div class="text-muted">${__("No open Purchase Orders or unpaid Purchase Invoices found.")}</div>`;
			if (pos.length || invoices.length) {
				html = "";
				if (pos.length) {
					html += `<b>${__("Open Purchase Orders")} (${pos.length})</b><ul>`;
					pos.forEach((po) => {
						html += `<li><a href="/app/purchase-order/${po.name}">${po.name}</a> — ${po.status} — ${format_currency(po.grand_total)}</li>`;
					});
					html += "</ul>";
				}
				if (invoices.length) {
					html += `<b>${__("Unpaid Purchase Invoices")} (${invoices.length})</b><ul>`;
					invoices.forEach((pi) => {
						html += `<li><a href="/app/purchase-invoice/${pi.name}">${pi.name}</a> — ${__("Outstanding")}: ${format_currency(pi.outstanding_amount)}</li>`;
					});
					html += "</ul>";
				}
			}
			frm.set_df_property("open_transactions_summary", "options", html);
			frm.refresh_field("open_transactions_summary");
		});

		if (frm.doc.docstatus === 0 && frm.doc.status !== "Vendor Disabled") {
			frm.add_custom_button(__("Disable Vendor Now"), () => {
				frappe.confirm(
					__("This disables the vendor immediately, ahead of the site's default sequencing. Continue?"),
					() => frm.call("disable_vendor_now").then(() => frm.reload_doc())
				);
			});
		}
	},
});
