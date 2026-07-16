# ============================================================
# Announcement Idea taxonomy + rule-based keyword scoring config
# Single source of truth — used to seed the DB tables and to
# score announcements. Edit weights/phrases here only.
# ============================================================

# raw score for an idea_type = sum(weight) of every keyword phrase
# found in the announcement subject (case-insensitive substring),
# PLUS a category bonus if the BSE category/subcategory text
# matches one of this idea's `category_hints`.
# If any `negative` phrase is found, the raw score is zeroed out
# (disqualified) regardless of positive matches.

CATEGORY_BONUS_WEIGHT = 1.5
MIN_SCORE_THRESHOLD = 20.0  # below this, we don't record the idea at all

GROUPS = [
    "Growth & Expansion",
    "Restructuring & Strategic Deals",
    "Fundraising & Corporate Actions",
    "Business & Operational Updates",
    "Shareholding & Ratings",
    "Regulatory & Legal",
    "Corporate Governance",
]

IDEA_TYPES = {

    # ---------------- Growth & Expansion ----------------
    "New Order win": {
        "group": "Growth & Expansion",
        "description": "Receipt of new order or contract.",
        "keywords": [
            ("letter of award", 2.0), ("receipt of order", 2.0), ("receives order", 2.0),
            ("bags order", 2.0), ("secures order", 1.5), ("order win", 1.5),
            ("awarded contract", 1.5), ("purchase order", 1.0), ("work order", 1.0),
            ("new order", 1.0), ("loa from", 1.5), ("contract from", 1.0),
        ],
        "negative": ["cancellation of order", "order cancelled", "loss of order", "termination of order"],
        "category_hints": ["award of order", "receipt of order"],
    },
    "New Capex / Expansion": {
        "group": "Growth & Expansion",
        "description": "Company announced capital expenditure or expansion.",
        "keywords": [
            ("capital expenditure", 2.0), ("capacity expansion", 2.0), ("expansion of capacity", 1.5),
            ("greenfield", 1.5), ("brownfield", 1.5), ("new plant", 1.0),
            ("setting up a plant", 1.5), ("capex plan", 1.5), ("expand production", 1.0),
            ("enhance capacity", 1.0), ("investment of", 0.5),
        ],
        "negative": [],
        "category_hints": ["capital expenditure", "expansion"],
    },
    "Facility / Plant commissioning": {
        "group": "Growth & Expansion",
        "description": "A new facility or production line has become operational.",
        "keywords": [
            ("commissioning of", 2.0), ("commences commercial production", 2.0),
            ("commenced commercial operations", 2.0), ("plant commissioned", 2.0),
            ("unit commissioned", 1.5), ("trial production", 1.0), ("commenced operations", 1.0),
        ],
        "negative": [],
        "category_hints": ["commissioning"],
    },
    "Product Launch / Innovation": {
        "group": "Growth & Expansion",
        "description": "Company announced a new product or innovation / R&D update.",
        "keywords": [
            ("launch of", 1.5), ("new product launch", 2.0), ("unveils", 1.5),
            ("introduces new", 1.5), ("product launch", 2.0), ("patent granted", 1.5),
            ("r&d update", 1.0), ("innovation", 0.5),
        ],
        "negative": [],
        "category_hints": ["new product"],
    },
    "New Venture / Subsidiary": {
        "group": "Growth & Expansion",
        "description": "Formation of new subsidiary, joint venture or new business vertical.",
        "keywords": [
            ("incorporation of subsidiary", 2.0), ("new subsidiary", 1.5),
            ("wholly owned subsidiary", 1.5), ("step down subsidiary", 1.5),
            ("formation of subsidiary", 2.0), ("new business vertical", 1.5),
            ("incorporated a company", 1.0),
        ],
        "negative": [],
        "category_hints": ["incorporation", "subsidiary"],
    },

    # ---------------- Restructuring & Strategic Deals ----------------
    "Acquisition": {
        "group": "Restructuring & Strategic Deals",
        "description": "Acquisition of another company, business unit, asset or equity stake.",
        "keywords": [
            ("acquisition of", 2.0), ("agreement to acquire", 2.0), ("acquiring", 1.0),
            ("acquired", 1.0), ("stake acquisition", 1.5), ("purchase of shares of", 1.5),
        ],
        "negative": ["divest", "sale of stake"],
        "category_hints": ["acquisition"],
    },
    "Merger / De-merger / Amalgamation": {
        "group": "Restructuring & Strategic Deals",
        "description": "Corporate restructuring such as merger, de-merger, spin-off or amalgamation.",
        "keywords": [
            ("merger", 2.0), ("amalgamation", 2.0), ("demerger", 2.0), ("de-merger", 2.0),
            ("scheme of arrangement", 1.5), ("spin-off", 1.5), ("spin off", 1.5),
        ],
        "negative": [],
        "category_hints": ["scheme of arrangement", "merger", "demerger", "amalgamation"],
    },
    "JV / MoU / Partnership": {
        "group": "Restructuring & Strategic Deals",
        "description": "Joint venture, memorandum of understanding or strategic partnership announcement.",
        "keywords": [
            ("memorandum of understanding", 2.0), (" mou ", 1.5), ("joint venture", 1.5),
            ("strategic partnership", 2.0), ("collaboration agreement", 1.5), ("tie-up", 1.0),
            ("partnership with", 1.0),
        ],
        "negative": [],
        "category_hints": ["joint venture", "mou", "partnership"],
    },
    "Divestment": {
        "group": "Restructuring & Strategic Deals",
        "description": "Divestment or sale of business/assets/stake.",
        "keywords": [
            ("divestment", 2.0), ("divest", 1.5), ("stake sale", 1.5), ("sale of stake", 2.0),
            ("disposal of", 1.0), ("sell its stake", 1.5), ("exit from", 1.0),
        ],
        "negative": ["acquisition of"],
        "category_hints": ["divestment", "sale of stake"],
    },

    # ---------------- Fundraising & Corporate Actions ----------------
    "Fund Raising": {
        "group": "Fundraising & Corporate Actions",
        "description": "Equity or debt fundraising such as QIP, private placement or rights issue.",
        "keywords": [
            ("qualified institutional placement", 2.0), (" qip", 1.5), ("rights issue", 2.0),
            ("preferential allotment", 2.0), ("private placement", 1.5), ("fund raising", 1.5),
            ("raise funds", 1.0), ("issue of debentures", 1.5), ("ncd issue", 1.0), ("fpo", 1.5),
        ],
        "negative": [],
        "category_hints": ["fund raising", "qip", "rights issue", "preferential issue"],
    },
    "Corporate Action": {
        "group": "Fundraising & Corporate Actions",
        "description": "Dividend, bonus, stock split or other corporate action.",
        "keywords": [
            ("dividend", 1.5), ("bonus shares", 2.0), ("stock split", 2.0),
            ("sub-division of shares", 1.5), ("record date", 1.0),
        ],
        "negative": ["buyback", "repurchase"],
        "category_hints": ["dividend", "bonus", "stock split", "corporate action"],
    },
    "ESOP / Employee Benefit": {
        "group": "Fundraising & Corporate Actions",
        "description": "Employee benefit related announcements.",
        "keywords": [
            ("esop", 2.0), ("employee stock option", 2.0), ("sweat equity", 1.5),
            ("employee benefit scheme", 1.5),
        ],
        "negative": [],
        "category_hints": ["esop", "employee stock option"],
    },
    "Open Offer / Takeover": {
        "group": "Fundraising & Corporate Actions",
        "description": "Open offer, takeover bid or related public offer activity.",
        "keywords": [
            ("open offer", 2.0), ("takeover", 1.5), ("public announcement", 1.0),
            ("acquirer", 1.0), ("sast regulations", 1.5),
        ],
        "negative": [],
        "category_hints": ["open offer", "takeover"],
    },
    "Buyback": {
        "group": "Fundraising & Corporate Actions",
        "description": "Company announced repurchase of shares (buyback).",
        "keywords": [
            ("buyback", 2.0), ("buy-back", 2.0), ("repurchase of shares", 2.0),
        ],
        "negative": [],
        "category_hints": ["buyback"],
    },

    # ---------------- Business & Operational Updates ----------------
    "Business Update": {
        "group": "Business & Operational Updates",
        "description": "Quarterly or monthly business update with operational data.",
        "keywords": [
            ("business update", 2.0), ("operational update", 1.5), ("monthly sales", 1.5),
            ("quarterly update", 1.5), ("provisional numbers", 1.0), ("sales update", 1.5),
            ("production update", 1.0),
        ],
        "negative": [],
        "category_hints": ["business update"],
    },
    "First Concall / Investor Presentation": {
        "group": "Business & Operational Updates",
        "description": "First concall or presentation by company post listing.",
        "keywords": [
            ("maiden concall", 2.0), ("first concall", 2.0), ("maiden investor", 2.0),
            ("first investor presentation", 2.0), ("post listing concall", 1.5),
        ],
        "negative": [],
        "category_hints": ["investor presentation", "conference call"],
    },
    "First Annual Report": {
        "group": "Business & Operational Updates",
        "description": "First annual report by company post listing.",
        "keywords": [
            ("first annual report", 2.0), ("maiden annual report", 2.0),
        ],
        "negative": [],
        "category_hints": ["annual report"],
    },

    # ---------------- Shareholding & Ratings ----------------
    "Promoter Action": {
        "group": "Shareholding & Ratings",
        "description": "Promoter-related actions such as insider trades, pledge, or stake movement.",
        "keywords": [
            ("pledge of shares", 2.0), ("release of pledge", 1.5), ("promoter group", 1.0),
            ("promoter", 0.5), ("sale by promoter", 1.5), ("acquisition by promoter", 1.5),
        ],
        "negative": [],
        "category_hints": ["promoter", "pledge", "insider trading"],
    },
    "Major Shareholding Change": {
        "group": "Shareholding & Ratings",
        "description": "Significant changes in shareholdings.",
        "keywords": [
            ("substantial acquisition", 1.5), ("change in shareholding", 2.0),
            ("shareholding pattern", 1.0), ("crosses 5%", 1.5), ("sast disclosure", 1.5),
        ],
        "negative": [],
        "category_hints": ["shareholding", "sast"],
    },
    "Credit Rating": {
        "group": "Shareholding & Ratings",
        "description": "Credit rating upgrade, downgrade or rating outlook change.",
        "keywords": [
            ("credit rating", 2.0), ("rating upgrade", 2.0), ("rating downgrade", 2.0),
            ("care ratings", 1.0), ("crisil", 1.0), ("icra", 1.0), ("rating outlook", 1.5),
            ("reaffirms rating", 1.5),
        ],
        "negative": [],
        "category_hints": ["credit rating"],
    },

    # ---------------- Regulatory & Legal ----------------
    "Regulatory Approval": {
        "group": "Regulatory & Legal",
        "description": "Receipt of government or regulatory approval, i.e. US FDA.",
        "keywords": [
            ("usfda", 2.0), ("us fda", 2.0), ("regulatory approval", 2.0),
            ("approval from", 1.0), ("nod from", 1.0), ("clearance from", 1.0),
            ("gmp certification", 1.5), ("who-gmp", 1.5), ("environmental clearance", 1.5),
            ("license granted", 1.0),
        ],
        "negative": [],
        "category_hints": ["regulatory approval", "fda"],
    },
    "Litigation / Dispute": {
        "group": "Regulatory & Legal",
        "description": "Litigation, dispute, court case, settlement or legal notice disclosure.",
        "keywords": [
            ("litigation", 2.0), ("court case", 1.5), ("lawsuit", 1.5), ("legal notice", 1.5),
            ("dispute", 1.0), ("arbitration", 1.5), ("show cause notice", 1.5),
            ("penalty imposed", 1.5),
        ],
        "negative": [],
        "category_hints": ["litigation", "penalty"],
    },
    "Clarification": {
        "group": "Regulatory & Legal",
        "description": "Clarification or rebuttal by company about rumors, media reports or speculative items.",
        "keywords": [
            ("clarification", 2.0), ("rebuttal", 1.5), ("denies", 1.5), ("media report", 1.5),
            ("news report", 1.0), ("speculative", 1.0), ("clarify", 1.0),
        ],
        "negative": [],
        "category_hints": ["clarification"],
    },

    # ---------------- Corporate Governance ----------------
    "Auditor Change": {
        "group": "Corporate Governance",
        "description": "Appointment, resignation or change of statutory or internal auditor.",
        "keywords": [
            ("statutory auditor", 2.0), ("resignation of auditor", 2.0),
            ("appointment of auditor", 2.0), ("internal auditor", 1.5), ("change of auditor", 1.5),
        ],
        "negative": [],
        "category_hints": ["auditor"],
    },
    "Management Change": {
        "group": "Corporate Governance",
        "description": "Appointment, resignation or change of key managerial personnel.",
        "keywords": [
            ("managing director", 1.5), ("chief executive officer", 1.5), ("chief financial officer", 1.5),
            ("key managerial personnel", 2.0), (" kmp ", 1.0), ("ceo", 1.0), ("cfo", 1.0),
            ("resignation of", 0.5), ("appointment of", 0.5),
        ],
        "negative": [],
        "category_hints": ["managing director", "kmp", "chief executive"],
    },
    "Company Secretary Change": {
        "group": "Corporate Governance",
        "description": "Appointment, resignation or change of company secretary or compliance officer.",
        "keywords": [
            ("company secretary", 2.0), ("compliance officer", 2.0),
        ],
        "negative": [],
        "category_hints": ["company secretary"],
    },
    "Board Change": {
        "group": "Corporate Governance",
        "description": "Appointment, resignation or change in board of directors.",
        "keywords": [
            ("board of directors", 1.0), ("director resigns", 2.0), ("appointment as director", 2.0),
            ("independent director", 1.5), ("additional director", 1.5), ("cessation of directorship", 2.0),
        ],
        "negative": ["company secretary"],
        "category_hints": ["director"],
    },
}
