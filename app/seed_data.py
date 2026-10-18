"""50 fictional ZAKHEERA employees. Fields: code, name, title, department, grade, status, location,
manager code, join date, monthly gross (PKR; stipend for interns), end date (interns and contracts)."""
from datetime import date

D = date
E = [
 ("0001","Syed Usman Ali","Chief Executive Officer","Executive",7,"Permanent","Karachi",None,D(2019,3,1),1800000,None),
 ("0002","Sadia Rahman","Head of Human Resources","Human Resources",6,"Permanent","Karachi","0001",D(2019,6,10),750000,None),
 ("0003","Imran Qureshi","Chief Technology Officer","Engineering",7,"Permanent","Karachi","0001",D(2019,3,1),1350000,None),
 ("0004","Farah Naeem","Chief Financial Officer","Finance",7,"Permanent","Karachi","0001",D(2019,9,2),1250000,None),
 ("0005","Bilal Ahsan","Chief Operating Officer","Operations and Admin",7,"Permanent","Karachi","0001",D(2020,1,6),1150000,None),
 ("0006","Mariam Javed","HR Business Partner","Human Resources",5,"Permanent","Karachi","0002",D(2021,2,15),480000,None),
 ("0007","Hassan Raza","Payroll and Benefits Officer","Human Resources",3,"Permanent","Karachi","0002",D(2022,8,1),210000,None),
 ("0008","Anum Tariq","Talent Acquisition Specialist","Human Resources",3,"Permanent","Lahore","0002",D(2023,5,22),195000,None),
 ("0009","Zoya Fatima","HR Intern","Human Resources",1,"Intern","Karachi","0002",D(2026,7,1),45000,D(2026,12,31)),
 ("0010","Salman Ghani","Engineering Manager","Engineering",5,"Permanent","Karachi","0003",D(2020,10,12),620000,None),
 ("0011","Kiran Shah","Engineering Manager","Engineering",5,"Permanent","Lahore","0003",D(2021,5,3),600000,None),
 ("0012","Ahmed Raza","Senior Software Engineer","Engineering",4,"Permanent","Karachi","0010",D(2022,2,14),380000,None),
 ("0013","Hira Baig","Software Engineer","Engineering",3,"Permanent","Karachi","0010",D(2023,9,4),245000,None),
 ("0014","Danish Malik","Software Engineer","Engineering",3,"Permanent","Karachi","0010",D(2024,3,18),225000,None),
 ("0015","Rabia Anwar","Associate Software Engineer","Engineering",2,"Probation","Karachi","0010",D(2026,8,10),130000,None),
 ("0016","Saad Yousuf","Software Engineering Intern","Engineering",1,"Intern","Karachi","0010",D(2026,6,1),50000,D(2026,11,30)),
 ("0017","Nimra Aslam","Senior Software Engineer","Engineering",4,"Permanent","Lahore","0011",D(2021,11,8),395000,None),
 ("0018","Faisal Chaudhry","Software Engineer","Engineering",3,"Permanent","Lahore","0011",D(2023,1,16),240000,None),
 ("0019","Maham Siddiqui","Associate Software Engineer","Engineering",2,"Permanent","Lahore","0011",D(2025,2,3),140000,None),
 ("0020","Hamza Iqbal","Software Engineer","Engineering",3,"Contract","Lahore","0011",D(2026,2,2),260000,D(2027,3,31)),
 ("0021","Sana Khalid","QA Engineer","Engineering",3,"Permanent","Lahore","0011",D(2024,7,1),205000,None),
 ("0022","Asad Mirza","Director of AI","Data Science and AI",6,"Permanent","Karachi","0003",D(2020,4,20),900000,None),
 ("0023","Zainab Hussain","Lead Data Scientist","Data Science and AI",5,"Permanent","Karachi","0022",D(2021,8,30),560000,None),
 ("0024","Omar Farooq","Senior ML Engineer","Data Science and AI",4,"Permanent","Karachi","0023",D(2022,10,3),410000,None),
 ("0025","Ayesha Noor","Data Scientist","Data Science and AI",3,"Permanent","Karachi","0023",D(2024,1,8),265000,None),
 ("0026","Taha Rehman","Data Analyst","Data Science and AI",2,"Permanent","Karachi","0023",D(2024,9,23),150000,None),
 ("0027","Hadia Salman","Data Science Intern","Data Science and AI",1,"Intern","Karachi","0023",D(2026,8,1),48000,D(2027,1,31)),
 ("0028","Rizwan Sheikh","MLOps Engineer","Data Science and AI",3,"Permanent","Islamabad","0022",D(2023,6,12),270000,None),
 ("0029","Kamran Abbasi","IT and Security Lead","IT and Security",5,"Permanent","Karachi","0003",D(2021,3,22),520000,None),
 ("0030","Noman Ali","DevOps Engineer","IT and Security",3,"Permanent","Karachi","0029",D(2023,11,13),255000,None),
 ("0031","Sidra Butt","IT Support Engineer","IT and Security",2,"Probation","Karachi","0029",D(2026,7,20),110000,None),
 ("0032","Mehwish Tahir","Head of Product","Product",6,"Permanent","Karachi","0001",D(2020,9,14),780000,None),
 ("0033","Shahzaib Anwar","Product Manager","Product",4,"Permanent","Karachi","0032",D(2022,5,9),400000,None),
 ("0034","Laiba Imran","UX Designer","Product",3,"Probation","Karachi","0032",D(2026,9,1),190000,None),
 ("0035","Raza Hashmi","Head of Sales","Sales and Business Development",6,"Permanent","Islamabad","0001",D(2020,2,17),760000,None),
 ("0036","Areeba Zafar","Account Manager","Sales and Business Development",4,"Permanent","Islamabad","0035",D(2022,7,18),340000,None),
 ("0037","Junaid Akhtar","Business Development Executive","Sales and Business Development",2,"Permanent","Lahore","0035",D(2025,4,7),145000,None),
 ("0038","Mahnoor Saeed","Sales Associate","Sales and Business Development",2,"Contract","Karachi","0035",D(2026,3,2),120000,D(2027,1,31)),
 ("0039","Hina Pervez","Marketing Manager","Marketing",5,"Permanent","Karachi","0005",D(2021,10,4),470000,None),
 ("0040","Waleed Aziz","Content Strategist","Marketing",3,"Permanent","Karachi","0039",D(2024,5,13),185000,None),
 ("0041","Eman Rashid","Marketing Intern","Marketing",1,"Intern","Karachi","0039",D(2026,9,1),42000,D(2027,2,28)),
 ("0042","Adeel Mansoor","Finance Manager","Finance",5,"Permanent","Karachi","0004",D(2020,11,2),540000,None),
 ("0043","Sumbul Fareed","Accountant","Finance",3,"Permanent","Karachi","0042",D(2023,3,6),215000,None),
 ("0044","Talha Zubair","Accounts Executive","Finance",2,"Probation","Karachi","0042",D(2026,9,15),115000,None),
 ("0045","Iqra Waseem","Finance Intern","Finance",1,"Intern","Karachi","0042",D(2026,7,15),45000,D(2027,1,14)),
 ("0046","Nadeem Jamil","Admin and Facilities Manager","Operations and Admin",5,"Permanent","Karachi","0005",D(2020,7,27),450000,None),
 ("0047","Shumaila Gul","Office Administrator","Operations and Admin",2,"Permanent","Karachi","0046",D(2022,12,5),125000,None),
 ("0048","Rehan Latif","Transport Coordinator","Operations and Admin",2,"Permanent","Karachi","0046",D(2024,2,12),118000,None),
 ("0049","Fizza Kamran","Executive Assistant to the CEO","Executive",3,"Permanent","Karachi","0001",D(2022,3,14),230000,None),
 ("0050","Owais Hameed","Procurement Officer","Operations and Admin",3,"Permanent","Karachi","0005",D(2023,8,21),205000,None),
]
# HR staff get HR-level access; the HR intern deliberately does not. The CEO has executive access.
# Everyone else is a manager if someone reports to them, otherwise a plain employee.
ACCESS_OVERRIDES = {"0001": "executive", "0002": "hr", "0006": "hr", "0007": "hr", "0008": "hr", "0009": "employee"}
FIXED = {   # code -> overrides applied after random generation
    "0014": {"rating": 2, "pip": True}, "0026": {"rating": 2, "pip": True},
    "0012": {"rating": 5}, "0001": {"rating": None}, "0049": {"rating": 4},
    "0023": {"bond": (120000, D(2027, 3, 31))}, "0025": {"bond": (90000, D(2027, 2, 28))},
    "0033": {"bond": (150000, D(2027, 6, 30))},
}
HAJJ = {"0001", "0042"}
WORK_MODEL_OVERRIDES = {"0028": "Remote", "0046": "Office", "0047": "Office", "0048": "Office", "0031": "Office"}
