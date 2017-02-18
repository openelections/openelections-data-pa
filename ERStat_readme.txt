Commonwealth of Pennsylvania
Department of State
Bureau of Commissions, Elections and Legislation
Precinct Election Returns
Electronic Data File Transmittal and Instructions
bcel@state.pa.us
www.dos.state.pa.us


Data Extracted:                 January 27, 2017
Requested Year                  2016
Requested Election:            	General
Record Count:                   203,588


The text data file, prcres.txt, is an ASCII text, comma delimited
file.  The file can also be easily imported into most PC database
and spreadsheet applications.  The field descriptions, maximum
length, and data type are listed below in the order in which they
appear (from left to right) in the file.  


Field Description                  	Length       	Data Type
-----------------------------------------------------------------
Election Year                   4       		Numeric
Election Type (G = General)	1       		Character
County Code *			2       		Numeric
Precinct Code			7       		Numeric
Candidate Office Rank		2       		Numeric
Candidate District		3       		Numeric
Candidate Party Rank		2       		Numeric
Candidate Ballot Position	2       		Numeric
Candidate Office Code *		3       		Character
Candidate Party Code*		3       		Character
Candidate Number		7       		Numeric
Candidate Last Name		50      		Character
Candidate First Name		50      		Character
Candidate Middle Name		50      		Character
Candidate Suffix		10       		Character
Vote Total			7       		Numeric
U.S. Congressional District	2       		Numeric
State Senatorial District	2       		Numeric
State House District		3       		Numeric
Municipality Type Code *	1       		Numeric
Municipality Name		23      		Character
Municipality Breakdown Code 1	1       		Character
Municipality Breakdown Name 1	21      		Character
Municipality Breakdown Code 2	1       		Character
Municipality Breakdown Name 2	21      		Character
Bi-County Code **		2       		Numeric
MCD Code			3       		Numeric
FIPS Code			3       		Numeric
VTD code			4       		Numeric
Previous Precinct Code		7       		Numeric
Previous U.S. Congressional District	2       		Numeric
Previous State Senatorial District	2       		Numeric
Previous State House District	3       		Numeric
--------------------------------------------------------------
 * See tables below.
** If a municipality is located in more than 1 county (i.e. it 
   crosses county lines), this is the code of the other county.


Election Type Table
-------------------
P Primary
G General
M Municipal
S Special


County Code Table
-----------------
01 Adams
02 Allegheny
03 Armstrong
04 Beaver
05 Bedford
06 Berks
07 Blair
08 Bradford
09 Bucks
10 Butler
11 Cambria
12 Cameron
13 Carbon
14 Centre
15 Chester
16 Clarion
17 Clearfield
18 Clinton
19 Columbia
20 Crawford
21 Cumberland
22 Dauphin
23 Delaware
24 Elk
25 Erie
26 Fayette
27 Forest
28 Franklin
29 Fulton
30 Greene
31 Huntingdon
32 Indiana
33 Jefferson
34 Juniata
35 Lackawanna
36 Lancaster
37 Lawrence
38 Lebanon
39 Lehigh
40 Luzerne
41 Lycoming
42 McKean
43 Mercer
44 Mifflin
45 Monroe
46 Montgomery
47 Montour
48 Northampton
49 Northumberland
50 Perry
51 Philadelphia
52 Pike
53 Potter
54 Schuylkill
55 Snyder
56 Somerset
57 Sullivan
58 Susquehanna
59 Tioga
60 Union
61 Venango
62 Warren
63 Washington
64 Wayne
65 Westmoreland
66 Wyoming
67 York


Municipality Type Codes
-----------------------
2 City
4 Township
5 Town
6 Borough


Municipality Breakdown Codes
----------------------------
D District
W Ward
P Precinct
X Other



Office Code Table
------------------------------------------------------------
1	USP	President Of The United States
2	USS	United States Senator
3	USC	Representative In Congress 
4	STS	Senator In The General Assembly 
5	STH	Representative In The General Assembly 
6	ATT	Attorney General 
7	AUD	Auditor General 
8	TRE	State Treasurer 
9	DED	Delegate To Democratic National Convention 
10	DER	Delegate To Republican National Convention 
11	ADD	Alt Delegate To Democratic National Convention
12	ADR	Alt Delegate To Republican National Convention

Party Code Table
--------------------------
D/R	Democratic / Republican
DEM	Democratic
REP	Republican