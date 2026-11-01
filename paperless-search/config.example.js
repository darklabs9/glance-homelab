// Copy this file to config.js (same folder) and adjust. Every key is optional:
// if config.js is missing, the pages fall back to the defaults below.
window.PAPERLESS_SEARCH_CONFIG = {
  // Link shown top-left on documents.html and scan.html (e.g. your Glance dashboard). Empty = no link.
  backUrl: "https://glance.example.lan/",
  backLabel: "Dashboard",

  // Locale used for dates. null = the browser's locale.
  locale: null,

  // Where pdf.js is loaded from (pdf.min.js + pdf.worker.min.js must both live there).
  // Default: cdnjs, pinned to 3.11.174. To self-host, see the README and set e.g. "./pdfjs/".
  pdfjsBase: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/",

  // jsPDF (used by scan.html to merge photos into one PDF).
  jspdfUrl: "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js",

  // "Expiring soon" section: a Paperless custom field of type Date, looked up by name
  // (case-insensitive). You can also give its numeric id. null/"" hides the section.
  expiryField: "Expiry date",
  expiryWindowDays: 120,   // show documents expiring within this many days (and already expired ones)
  expiryMax: 6,            // max rows shown

  // Saved views are shown grouped. Names are matched case-insensitively, emojis/punctuation ignored.
  // Views not listed here end up in "Other". Views pointing at the inbox tag are always shown first
  // and open the "to sort" mode.
  viewGroups: [
    ["Family", ["school", "id documents", "health", "holidays"]],
    ["Home and money", ["bills", "house", "car", "taxes", "bank"]],
    ["Shopping", ["vouchers", "receipts"]],
    ["Work", ["work"]]
  ],

  // Icon per saved view name. Available icons: doc, school, gift, invoice, ticket, work, home,
  // car, taxes, health, id, holiday. Unknown names get "doc".
  viewIcons: {
    "school": "school", "vouchers": "gift", "bills": "invoice", "receipts": "ticket", "work": "work",
    "house": "home", "car": "car", "taxes": "taxes", "health": "health", "id documents": "id", "holidays": "holiday"
  },

  // Hint shown in the search field.
  searchPlaceholder: "Search: invoice, insurance, warranty…"
};
