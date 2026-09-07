// Copyright (c) 2026, Auriga IT and contributors
// For license information, please see license.txt

frappe.listview_settings["Vendor Support Ticket"] = {
	// get_indicator() isn't just for the list view — frappe.get_indicator()
	// (used for the status pill next to the title on the Desk form itself)
	// reads this same function, so this one definition colors both.
	get_indicator: function (doc) {
		var colors = {
			Open: "grey",
			"In Progress": "blue",
			Resolved: "orange",
			Reopened: "red",
			Closed: "green",
			Invalid: "darkgrey",
		};
		return [__(doc.status), colors[doc.status] || "grey", "status,=," + doc.status];
	},
};
