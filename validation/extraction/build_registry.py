"""Regenerate validation/registry/sources.yaml from the local evidence tree."""

import hashlib
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
EV = REPO / "validation" / "evidence"
OUT = REPO / "validation" / "registry" / "sources.yaml"

# Documents the extraction scripts actually consume, with their public identity.
DESCRIBED = {
    "catalogs/alfalaval_tyrva_airsock_unit_coolers.pdf": dict(
        title="TYR-A air sock unit coolers",
        publisher="Alfa Laval",
        doc_id="ERC00369EN-1112",
        rating="EN 328 SC2 (R-404A, t0 -8 C, air 0 C, DT1 8 K, frosted)",
        used_by="en328_inversion",
        url="https://www.alfalaval.com/",
    ),
    "catalogs/guntner_ghf_unit_coolers.pdf": dict(
        title="GHF.2 high-efficiency unit coolers",
        publisher="Guentner",
        doc_id="GHF.2",
        rating="EN 328 SC2 (R-404A, t0 -8 C, DT1 8 K)",
        used_by="en328_inversion",
        url="https://www.guentner.com/",
    ),
    "catalogs/guntner_gacc_air_cooler_datasheet.pdf": dict(
        title="Cubic Compact GACC air coolers",
        publisher="Guentner",
        doc_id="GACC",
        rating="EN 328 SC2 (R-404A, t0 -8 C, DT1 8 K), 50 Hz and 60 Hz tables",
        used_by="en328_inversion",
        url="https://www.guentner.com/",
    ),
    "catalogs/gea_searle_coolers_condensing_units.pdf": dict(
        title="Cooler and Condensing Unit Ranges",
        publisher="GEA Searle",
        doc_id="-",
        rating="EN 328 SC2 (R-404A, t0 -8 C, air 0 C, DT1 8 K); series JG TEC NS KEC KMe KLe",
        used_by="en328_inversion",
        url="https://www.gea.com/",
    ),
    "catalogs/alfalaval_acq504_condenser_spec.pdf": dict(
        title="AlfaGreen air-cooled condensers AC / ACD / ACV",
        publisher="Alfa Laval",
        doc_id="ECR00011EN (2003)",
        rating="ENV 327 (R-404A, air 25 C, condensing 40 C, DT1 15 K), declared verbatim",
        used_by="env327_inversion",
        url="https://www.alfalaval.com/",
    ),
    "catalogs/luve_air_cooled_condensers.pdf": dict(
        title="Air-Cooled Condensers",
        publisher="LU-VE",
        doc_id="-",
        rating="ENV 327 (R-404A, air 25 C, condensing 40 C, DT1 15 K)",
        used_by="env327_inversion",
        url="https://www.luvegroup.com/",
    ),
    "catalogs/trane_precedent_hp_pkgp_prc003.pdf": dict(
        title="Packaged Heat Pump Units Precedent 3-10 Tons",
        publisher="Trane",
        doc_id="PKGP-PRC003-EN (05/06)",
        rating="AHRI 210/240 or 340/360 cooling: 95 F ambient, 80/67 F entering; R-22",
        used_by="trane_geometry (Tables 1-3, outdoor coil geometry and fan flow)",
        url="https://www.trane.com/",
    ),
    "catalogs/trane_precedent_hp_wsc048_catalog.pdf": dict(
        title="Packaged Rooftop Air Conditioners Precedent - Heat Pump 3-10 Tons 60 Hz",
        publisher="Trane",
        doc_id="PKGP-PRC013K-EN (02/16/2015)",
        rating="AHRI cooling: 95 F ambient, 80/67 F entering; R-410A",
        used_by="trane_geometry (Tables 1-3, outdoor coil geometry and fan flow)",
        url="https://www.trane.com/",
    ),
    "catalogs/trane_precedent_hp_2004_pkgp.pdf": dict(
        title="Packaged Heat Pumps (September 2004)",
        publisher="Trane",
        doc_id="PKGP-PRC003-EN (09/04)",
        rating="predecessor printing, cross-check only",
        used_by="cross-check",
        url="https://www.trane.com/",
    ),
    "catalogs/daikin_rxm_a_databook_eeden24.pdf": dict(
        title="RXM-A engineering data book (pair application)",
        publisher="Daikin",
        doc_id="EEDEN24-200",
        rating="EN 14511; cooling and heating grids at nominal operating frequency",
        used_by="daikin_grids -> validation/catalogs/daikin_rxm*.yaml",
        url="https://www.daikin.eu/",
    ),
    "catalogs/fujitsu_asuh09lpas_dtm.pdf": dict(
        title="ASUH/AOUH LPAS design & technical manual",
        publisher="Fujitsu General",
        doc_id="-",
        rating="capacity grids, SI blocks; heating grids at maximum capacity",
        used_by="fujitsu_grids -> validation/catalogs/fujitsu_asuh*.yaml",
        url="https://www.fujitsu-general.com/",
    ),
    "data/hplib/csv": dict(
        title="Heat Pump Keymark declared performance records (hplib input/csv)",
        publisher="Forschungszentrum Juelich (hplib), MIT licence",
        doc_id="doi:10.5281/zenodo.5521597",
        rating="EN 14825 average climate, test points A-D, low and medium application",
        used_by="keymark_declared -> validation/data/keymark_en14825_summary.csv",
        url="https://github.com/FZJ-IEK3-VSA/hplib",
    ),
    "pdfs/guth_atakan_2023_ijrefrig.pdf": dict(
        title="Semi-empirical model of a variable speed scroll compressor for R-290",
        publisher="Int. J. Refrigeration 146, 483-499",
        doc_id="doi:10.1016/j.ijrefrig.2022.10.024",
        rating="-",
        used_by="compressor_efficiency (Table A.3 electro-mechanical shape)",
        url="https://doi.org/10.1016/j.ijrefrig.2022.10.024",
    ),
    "data/ossorio2023/1-s2.0-S1359431123007548-mmc2.xlsx": dict(
        title="Inverter efficiency measurements, supplementary data mmc2",
        publisher="Applied Thermal Engineering 233, 120725 (CC BY)",
        doc_id="doi:10.1016/j.applthermaleng.2023.120725",
        rating="drive efficiency vs output frequency, 15-110 Hz, three inverters",
        used_by="compressor_speed_losses",
        url="https://doi.org/10.1016/j.applthermaleng.2023.120725",
    ),
    "pdfs/elson_vehr_2006_icec1772.pdf": dict(
        title="Scroll compressor performance at low speed",
        publisher="Purdue ICEC",
        doc_id="ICEC 1772 (2006)",
        rating="-",
        used_by="compressor efficiency evidence",
        url="https://docs.lib.purdue.edu/icec/",
    ),
    "pdfs/dk_energy_agency_scop_nef_en14825.pdf": dict(
        title="SCOP / EN 14825 guidance",
        publisher="Danish Energy Agency",
        doc_id="-",
        rating="EN 14825 test-point conditions (secondary source)",
        used_by="plr coordinate definition",
        url="https://ens.dk/",
    ),
    # --- standalone-compressor maps (validation/compressor_maps, coefficients v2026-09-15b) ---
    "compressor_maps/copeland/opi": dict(
        title="Copeland Online Product Information -- AHRI 540 coefficient sets of variable-speed scrolls (GetCCoefficients JSON + CSummary)",
        publisher="Copeland LP",
        doc_id="webapps.copeland.com/online-product-information (accessed 2026-09-15)",
        rating="dew-point rating, 20 degF constant superheat, 15 degF subcooling, power at drive input; one record per model x rated speed",
        used_by="compressor_maps.fetch.copeland_opi -> parse.copeland_opi -> validation/data/compressor_maps/points_copeland_opi.csv",
        url="https://webapps.copeland.com/online-product-information/",
    ),
    "compressor_maps/copeland/ae1414_zpv066_zpv096.pdf": dict(
        title="AE-1414: ZPV066 & ZPV096 Copeland Scroll Variable Speed Compressors",
        publisher="Copeland LP",
        doc_id="AE-1414",
        rating="application bulletin (speed range, envelope); no performance table",
        used_by="compressor_maps context (speed range)",
        url="https://www.copeland.com/",
    ),
    "compressor_maps/copeland/aeb1402_zpv063.pdf": dict(
        title="AE bulletin: ZPV063 Copeland Scroll Variable Speed",
        publisher="Copeland LP",
        doc_id="AEB 1402",
        rating="-",
        used_by="compressor_maps context",
        url="https://www.copeland.com/",
    ),
    "compressor_maps/copeland/aeb1407_zpv021_041_zhv.pdf": dict(
        title="AE bulletin: ZPV021-041 / ZHV021-034 Copeland Scroll Variable Speed",
        publisher="Copeland LP",
        doc_id="AEB 1407",
        rating="-",
        used_by="compressor_maps context",
        url="https://www.copeland.com/",
    ),
    "compressor_maps/arep/AHRI_AREP_Rpt011_ORNL_Pub44928.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #11: Compressor calorimeter test of R-410A alternatives (R32, DR-5, L-41A)",
        publisher="Oak Ridge National Laboratory (Shrestha et al., 2013), public",
        doc_id="ORNL Pub44928",
        rating="Copeland ZP21K5E-PFV scroll 1.24 in3/rev, 60 Hz, calorimeter per ASHRAE 23-2010; appendix A: 12 tables, 15 degF subcooling, 20/40 degF superheat or 65 degF suction temperature",
        used_by="compressor_maps.parse.arep -> points_arep.csv (Tables A1-A12)",
        url="ORNL publications, Pub44928 (info.ornl.gov/sites/publications)",
    ),
    "compressor_maps/arep/AREP_Rpt024.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #24: Compressor calorimeter test of refrigerant DR-5 in a R-410A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Nicholson, 20 Aug 2013), public",
        doc_id="AHRI Low-GWP AREP Test Report #24",
        rating="Copeland ZP31K5E-PFV scroll 1.8 in3/rev, 3500 rpm nominal, 60 Hz; DR-5 row list (Tables 1 and 3, 22 points), 20 degF superheat, 15 degF subcooling; R-410A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (22 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt026.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #26: Compressor calorimeter test of refrigerant R-32 in a R-410A rotary type compressor",
        publisher="Shanghai Hitachi Electrical Appliances (Zhang, Zhu, Qin, 13 Oct 2013), public",
        doc_id="AHRI Low-GWP AREP Test Report #26",
        rating="Hermetic rotary 28.0 cc, constant speed 50 Hz, liquid-injection port added; Table 4 = four standard points x R-410A/R-32 (measured), Tables 5-6 = ten-coefficient polynomials, Table 3 = 26 reporting conditions",
        used_by="compressor_maps.parse.arep26_rotary -> points_arep26_rotary.csv (8 measured + 40 polynomial rows) and arep26_coefficients.json",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt033.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #33: Compressor calorimeter test of R-410A alternative R-32/R-134a mixture using a scroll compressor",
        publisher="Oak Ridge National Laboratory (Shrestha, Sharma, Abdelaziz, 18 Feb 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #33",
        rating="Copeland ZP21K5E-PFV scroll 1.24 in3/rev, 60 Hz; appendix A: 6 tables; all 364 cells identical to ORNL/TM-2013/414 (Pub46026)",
        used_by="compared cell by cell with points_arep.csv rows of ornl_tm_2013_414 (identical; not duplicated)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt034.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #34: Compressor calorimeter test of refrigerant DR-7 in a R-404A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Nicholson, 14 Apr 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #34",
        rating="Copeland ZF18K4E-TFD scroll 5.983 in3/rev, 3500 rpm nominal, 60 Hz; DR-7 row list (19 points), 20 degF superheat, 0 degF subcooling, no liquid injection; R-404A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (19 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt036.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #36: Compressor calorimeter test of refrigerant L-40 in a R-404A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Nicholson, 14 Apr 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #36",
        rating="Copeland ZF18K4E-TFD scroll 5.983 in3/rev, 3500 rpm nominal, 60 Hz; L-40 row list (19 points), 20 degF superheat, 0 degF subcooling, no liquid injection in the tables; R-404A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (19 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt038.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #38: Compressor calorimeter test of refrigerant L-41b in a R-410A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Nicholson, 14 Apr 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #38",
        rating="Copeland ZP31K5E-PFV scroll 1.8 in3/rev, 3500 rpm nominal, 60 Hz; L-41b row list (30 points), 20 degF superheat, 15 degF subcooling; R-410A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (30 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt039.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #39: Compressor calorimeter test of refrigerant R-32 in a R-410A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Nicholson, 14 Apr 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #39",
        rating="Copeland ZP31K5E-PFV scroll 1.8 in3/rev, 3500 rpm nominal, 60 Hz; pure R-32 row list (23 points), 20 degF superheat, 15 degF subcooling; R-410A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (23 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt040.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #40: Compressor calorimeter test of refrigerant DR-5 in a R-410A rotary type compressor",
        publisher="Shanghai Hitachi Electrical Appliances (Zhang, Zhu, 1 May 2014), public",
        doc_id="AHRI Low-GWP AREP Test Report #40",
        rating="Same 28.0 cc rotary as report #26, 50 Hz, no injection; Table 3 = four standard points x R-410A/DR-5; its R-410A column is identical to report #26 Table 4 (not an independent measurement)",
        used_by="compressor_maps.parse.arep26_rotary -> points_arep26_rotary.csv (4 DR-5 rows)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt058.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #58: Compressor calorimeter test of refrigerant DR-5A (R454B) in a R-410A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Pham, Bella, Skillen, 18 Jan 2016), public",
        doc_id="AHRI Low-GWP AREP Test Report #58",
        rating="Copeland ZP31K5E-PFV scroll 1.8 in3/rev, gas-cycle stand (no glide data); DR-5A row list (29 points), 20 degF superheat, 15 degF subcooling; R-410A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (29 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt065.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #65: Compressor calorimeter test of refrigerant L-41-2 (R-447A) in a R-410A scroll compressor",
        publisher="Emerson Climate Technologies (Rajendran, Pham, Bella, Skillen, 18 Jan 2016; tested in Mikulov), public",
        doc_id="AHRI Low-GWP AREP Test Report #65",
        rating="Copeland ZP122KCE-TFD scroll 6.853 in3/rev, 380-3-50, 2900 rpm nominal; R-447A row list (23 points), 20 R superheat, 15 R subcooling; R-410A baseline is published rating data, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist.csv (23 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/AREP_Rpt066.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #66: Compressor calorimeter test of refrigerant HPR2A in a R-410A scroll compressor",
        publisher="Danfoss Commercial Compressors, Tianjin (Suindykov, Zhang, Gernemann, 18 Jan 2016), public",
        doc_id="AHRI Low-GWP AREP Test Report #66",
        rating="Danfoss SH161A4 hermetic fixed-speed scroll, 400 V 50 Hz, 2900 rpm (displacement not printed); HPR2A Tables A1 (SI) and A2 (IP) with 28 points, 11.1 K superheat, 8.3 K subcooling at the bubble point; R-410A from published data plus validation points, not tabulated",
        used_by="compressor_maps.parse.arep_rowlist -> points_arep_rowlist_no_displacement.csv (28 points)",
        url="AHRI Low-GWP AREP report list (www.ahrinet.org/analytics/research/ahri-low-gwp-alternative-refrigerants-evaluation-program)",
    ),
    "compressor_maps/arep/Pub42278.pdf": dict(
        title="AHRI Low-GWP AREP Test Report #21: Compressor calorimeter test of R-404A alternatives ARM-31a, D2Y-65, L-40 and R-32/R-134a (50/50)",
        publisher="Oak Ridge National Laboratory (Shrestha, Sharma, Abdelaziz, 2013), public",
        doc_id="ORNL Pub42278",
        rating="Copeland scroll 3.11 in3/rev (not the 1.24 in3/rev unit), 60 Hz, 0 degF subcooling; appendix A: 15 tables",
        used_by="compressor_maps.parse.arep -> points_arep.csv (Tables A1-A15)",
        url="ORNL publications, Pub42278 (info.ornl.gov/sites/publications)",
    ),
    "compressor_maps/arep/Pub46026.pdf": dict(
        title="ORNL/TM-2013/414: Compressor calorimeter test of R-410A alternative R-32/R-134a (94.07/5.93)",
        publisher="Oak Ridge National Laboratory (2013), public",
        doc_id="ORNL/TM-2013/414 (Pub46026)",
        rating="Copeland ZP21K5E-PFV scroll 1.24 in3/rev, 60 Hz, 15 degF subcooling; appendix A: 6 tables",
        used_by="compressor_maps.parse.arep -> points_arep.csv (Tables A1-A6)",
        url="ORNL publications, Pub46026 (info.ornl.gov/sites/publications)",
    ),
    "compressor_maps/copeland/xpv_ypv_range.pdf": dict(
        title="Copeland XPV & ZPV variable speed scroll compressor ranges (GPC-EN-2024)",
        publisher="Copeland Europe",
        doc_id="GPC-EN-2024 p.29",
        rating="EN 12900 nominal (5/50 degC, SH 10 K, SC 0 K) at 5400 rpm; min/max capacity at Tc 50 degC",
        used_by="cross-check of OPI records (not parsed)",
        url="https://www.copeland.com/",
    ),
    "compressor_maps/papers/cuevas_lebrun_2009_ate.pdf": dict(
        title="Testing and modelling of a variable speed scroll compressor",
        publisher="Applied Thermal Engineering 29(2-3), 469-478",
        doc_id="doi:10.1016/j.applthermaleng.2008.03.016",
        rating="calorimeter tests, Tables 2-3: 18 network-fed at 50 Hz + 30 inverter-fed 35-75 Hz, with discharge temperature",
        used_by="compressor_maps.parse.cuevas2009 -> points_cuevas2009.csv; eta_em anchor (ETA_EM_REF)",
        url="https://doi.org/10.1016/j.applthermaleng.2008.03.016",
    ),
    "compressor_maps/dardenne2015/dardenne2015_polimi.pdf": dict(
        title="Semi-empirical modelling of a variable speed scroll compressor with vapour injection (author version)",
        publisher="Int. J. Refrigeration 54 (Dardenne, Molinaroli, Joppolo et al., 2015); POLIMI repository copy",
        doc_id="IJR 54 (2015); POLIMI 11311/958185",
        rating="EN 13771 calorimeter; R410A vapour-injection scroll, 5.3 m3/h at 50 Hz; Appendix Table A.1: 63 tests, 40-116.67 Hz",
        used_by="compressor_maps.parse.dardenne2015 -> points_dardenne2015.csv",
        url="https://re.public.polimi.it/handle/11311/958185",
    ),
    "compressor_maps/ossorio2019/ossorio2019_conf.pdf": dict(
        title="Efficiency characterization of a variable speed compressor (XI CNIT / II International Engineering Thermodynamics Congress)",
        publisher="Ossorio-Santiago & Navarro-Peris, UPV (author copy of the congress proceedings)",
        doc_id="UPV handle 10251/180707",
        rating="EN 13771-1 calorimeter; R290 rotary TPB306F, 30-120 rps, Te -7 / Tc 50 degC, SH 10 K, SC 4 K, APY and AKO inverters; Table 1: 18 tests",
        used_by="compressor_maps.parse.ossorio2019 -> points_ossorio2019_rotary.csv",
        url="https://riunet.upv.es/handle/10251/180707",
    ),
    "compressor_maps/tello2017/tello_newcharacterization.pdf": dict(
        title="New characterization methodology for vapor-injection scroll compressors (author version)",
        publisher="Tello-Oquendo et al., Int. J. Refrigeration (2017); UPV repository copy",
        doc_id="IJR 2017 (UPV author version)",
        rating="UNE-EN 13771-1 calorimeter; R407C vapour-injection scroll ZH18KVE-TFD 17.1 m3/h; SH 5 K, injection SH 5 K; Table 2: 16 tests",
        used_by="compressor_maps.parse.tello2017 -> points_tello2017_scvi.csv",
        url="https://riunet.upv.es/",
    ),
    "compressor_maps/tello2019/tello2019_thesis.pdf": dict(
        title="Tello-Oquendo doctoral thesis (UPV): characterisation and semi-empirical modelling of scroll compressors with and without vapour injection",
        publisher="Universitat Politecnica de Valencia (2021), 282 pages",
        doc_id="UPV handle 10251/120473",
        rating="Table 5.1 (98 cm3, 17.1 m3/h @ 50 Hz for the SCVI; C15/C21/C30/C38 34/46/67/82 cm3 R290); Table 5.2 test matrix only (no measured values for the non-injection scrolls)",
        used_by="displacement and speed of the SCVI for compressor_maps.parse.tello2017; checked for non-injection R290 point tables (none, figures only)",
        url="https://riunet.upv.es/handle/10251/120473",
    ),
    "compressor_maps/papers/ossorio_navarroperis_2023_ate.pdf": dict(
        title="Testing of variable-speed scroll compressors and their inverters for the development of empirical correlations",
        publisher="Applied Thermal Engineering 230, 120725 (CC BY)",
        doc_id="doi:10.1016/j.applthermaleng.2023.120725",
        rating="Table 2 compressor set (A: R290 46 cm3 70 Hz nominal; B: R410A 44.5 cm3 60 Hz; C: Cuevas R134a 54.25 cm3 50 Hz)",
        used_by="compressor_maps documentation (speed conventions, E1 drive-loss form)",
        url="https://doi.org/10.1016/j.applthermaleng.2023.120725",
    ),
    "compressor_maps/papers/purdue_icec_3807_rotary.pdf": dict(
        title="Modeling and Performance Evaluation of Rotary Compressor and Air-Conditioning System using Low GWP Refrigerants (ICEC 2022, paper 2748)",
        publisher="Purdue e-Pubs",
        doc_id="ICEC 2748 (2022)",
        rating="simulation study, 7.25 cc rotary at 60 rps; no tabulated map",
        used_by="screened, not used",
        url="https://docs.lib.purdue.edu/icec/2748",
    ),
    "compressor_maps/highly/highly_catalogue_2024.pdf": dict(
        title="Highly rotary compressors catalogue 2024 (heat pump, water heater, R290 inverter)",
        publisher="Shanghai Highly / JOAP",
        doc_id="-",
        rating="ASHRAE/T rated point at 3600 rpm (R290 inverter tables, pp. 2-3)",
        used_by="compressor_maps.parse.highly2024 -> points_highly2024.csv",
        url="https://joap.dk/",
    ),
    "compressor_maps/highly/highly_rotary_r290.pdf": dict(
        title="Highly rotary R290 compressors (presentation)",
        publisher="Shanghai Highly / JOAP",
        doc_id="-",
        rating="rated points only",
        used_by="cross-check of highly_catalogue_2024",
        url="https://joap.dk/",
    ),
    "compressor_maps/gmcc/gmcc_rotary_catalog_v2024.pdf": dict(
        title="GMCC rotary compressor product catalogue V2024",
        publisher="GMCC (Midea)",
        doc_id="-",
        rating="rated point per model (GX / ARI), no speed stated",
        used_by="screened, not used (rated speed unknown)",
        url="https://gmcccompressors.com/",
    ),
    "compressor_maps/lg/lg_rotary_catalogue.pdf": dict(
        title="LG rotary compressor catalogue",
        publisher="LG Electronics",
        doc_id="-",
        rating="rated point per model (ASHRAE/ARI/SET), no speed stated",
        used_by="screened, not used (rated speed unknown)",
        url="https://www.lg.com/global/business/compressor-motor",
    ),
    "compressor_maps/papers/shao_2004_ijr_rotary.pdf": dict(
        title="Performance representation of variable-speed compressor for inverter air conditioners based on experimental data",
        publisher="International Journal of Refrigeration 27(8), 805-815",
        doc_id="doi:10.1016/j.ijrefrig.2004.02.008",
        rating="manufacturer map polynomials (Eqs. 1-2, Table 1) and frequency corrections (Tables 2-3) of three inverter rotaries, 30-120 Hz; map 11 K superheat / 8.3 K subcooling; refrigerant R22 inferred",
        used_by="compressor_maps.parse.shao2004 -> points_shao2004.csv (eta_oi all three; eta_vol Mitsubishi only, the other two fail the displacement check)",
        url="https://doi.org/10.1016/j.ijrefrig.2004.02.008",
    ),
    "compressor_maps/mitsubishi/mitsubishi_rotary_catalogue.pdf": dict(
        title="Mitsubishi Electric rotary compressor catalogue",
        publisher="Mitsubishi Electric",
        doc_id="-",
        rating="fixed-speed rated points and displacements only",
        used_by="displacement cross-check for the Shao 2004 RHV207FEM row; screened otherwise",
        url="https://mitsubishicompressors.com/",
    ),
    "compressor_maps/mitsubishi/mitsubishi_rotary_catalogue_2023.pdf": dict(
        title="Mitsubishi Electric rotary compressor catalogue 2023",
        publisher="Mitsubishi Electric",
        doc_id="-",
        rating="fixed-speed rated points and displacements only",
        used_by="screened, not used",
        url="https://mitsubishicompressors.com/",
    ),
    "compressor_maps/toshiba/toshiba_rotary_catalog.pdf": dict(
        title="Toshiba Carrier rotary compressor catalogue",
        publisher="Toshiba Carrier",
        doc_id="-",
        rating="capacity ranges per model, no map",
        used_by="screened, not used",
        url="https://www.toshiba-carrier.co.jp/",
    ),
    "compressor_maps/toshiba/toshiba_review_2001_dc_twin_rotary.pdf": dict(
        title="Toshiba Review 2001: DC twin-rotary compressor",
        publisher="Toshiba",
        doc_id="-",
        rating="review article, efficiency curves as figures only",
        used_by="screened, not used",
        url="https://www.global.toshiba/",
    ),
    "compressor_maps/mitsubishi/kvb_reference_guide.pdf": dict(
        title="Mitsubishi Electric SCI inverter rotary compressor line-up (KVB reference guide)",
        publisher="Mitsubishi Electric",
        doc_id="-",
        rating="line-up sheet, no performance data",
        used_by="screened, not used",
        url="https://mitsubishicompressors.com/",
    ),
    "compressor_maps/danfoss_vzh_brochure.pdf": dict(
        title="Danfoss Inverter Scrolls VZH series brochure",
        publisher="Danfoss",
        doc_id="AD166386436872en-001207",
        rating="brochure; performance data only in Coolselector",
        used_by="screened, not used",
        url="https://www.danfoss.com/",
    ),
    "pdfs/ehpa_testreg_AA-HP_v1.3a_2021.pdf": dict(
        title="Heat Pump Keymark testing regulation, air-to-air",
        publisher="EHPA",
        doc_id="TestReg AA-HP v1.3a (2021)",
        rating="EN 14825 test-point conditions (secondary source)",
        used_by="plr coordinate definition",
        url="https://www.ehpa.org/",
    ),
}


# Bulk datasets are recorded as one aggregate row rather than one row per file.
# The hplib export alone is 2,634 certificates; listing each would bury the
# fifteen documents a reader actually needs to obtain.
BULK_DIRS = {
    "data/hplib/csv": "Heat Pump Keymark certificates (hplib input/csv)",
    "compressor_maps/copeland/opi": "Copeland OPI AHRI-540 coefficient records (JSON + index.csv)",
}


def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for c in iter(lambda: fh.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def pages(p):
    if p.suffix.lower() != ".pdf":
        return None
    try:
        out = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True, timeout=20).stdout
        for l in out.splitlines():
            if l.startswith("Pages:"):
                return int(l.split(":", 1)[1])
    except Exception:
        return None
    return None


header = """# Provenance registry for validation/evidence/
#
# The evidence tree itself is NOT tracked: publisher PDFs, page renders of
# copyrighted papers and supplementary datasets are not redistributable. This
# file is the tracked half -- it records what each document is, where to obtain
# it, and the checksum of the copy the extraction scripts were run against.
#
# A reader who obtains the same document from the publisher and gets the same
# sha256 is running on identical bytes, and every number under validation/data/
# follows from those bytes by the scripts in validation/extraction/.
#
# Regenerate with: uv run python -m validation.extraction.build_registry

documents:
"""


def main() -> None:
    """Rewrite the registry from the evidence tree, or refuse and leave it as it is.

    The evidence tree is not tracked, so on most checkouts it is absent or
    partial. Writing from what happens to be present would silently replace the
    committed registry with a shorter one; instead every described document must
    be present before anything is written.
    """
    if not EV.is_dir():
        raise SystemExit(
            f"build_registry: no evidence tree at {EV.relative_to(REPO)} (validation/evidence is not "
            "tracked); obtain the documents listed in validation/registry/sources.yaml first. "
            "The registry was left untouched."
        )

    described, undescribed = [], []
    bulk_counts: dict[str, tuple[int, int]] = {}
    for p in sorted(EV.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(EV))
        parent = next((d for d in BULK_DIRS if rel.startswith(d + "/")), None)
        if parent:
            count, size = bulk_counts.get(parent, (0, 0))
            bulk_counts[parent] = (count + 1, size + p.stat().st_size)
            continue
        (described if rel in DESCRIBED else undescribed).append((rel, p))

    found = {rel for rel, _ in described} | {rel for rel, (count, _) in bulk_counts.items() if count}
    missing = sorted(set(DESCRIBED) - found)
    if missing:
        raise SystemExit(
            f"build_registry: {len(missing)} described document(s) missing from "
            f"{EV.relative_to(REPO)}; writing now would drop them from the registry. "
            "The registry was left untouched. Missing:\n  " + "\n  ".join(missing)
        )

    lines = [header]
    for rel, d in ((r, DESCRIBED[r]) for r in DESCRIBED if r in BULK_DIRS):
        count, size = bulk_counts[rel]
        lines.append(f"  - path: {rel}/\n")
        lines.append(f'    title: "{d["title"]}"\n')
        lines.append(f'    publisher: "{d["publisher"]}"\n')
        lines.append(f'    document_id: "{d["doc_id"]}"\n')
        lines.append(f'    rating_condition: "{d["rating"]}"\n')
        lines.append(f'    used_by: "{d["used_by"]}"\n')
        lines.append(f'    obtain_from: "{d["obtain_from"] if "obtain_from" in d else d["url"]}"\n')
        lines.append(f"    files: {count}\n")
        lines.append(f"    bytes: {size}\n")
        lines.append("\n")

    for rel, p in described:
        d = DESCRIBED[rel]
        npages = pages(p)
        lines.append(f"  - path: {rel}\n")
        lines.append(f'    title: "{d["title"]}"\n')
        lines.append(f'    publisher: "{d["publisher"]}"\n')
        lines.append(f'    document_id: "{d["doc_id"]}"\n')
        lines.append(f'    rating_condition: "{d["rating"]}"\n')
        lines.append(f'    used_by: "{d["used_by"]}"\n')
        lines.append(f'    obtain_from: "{d["url"]}"\n')
        lines.append(f"    bytes: {p.stat().st_size}\n")
        if npages:
            lines.append(f"    pages: {npages}\n")
        lines.append(f'    sha256: "{sha(p)}"\n')
        lines.append("\n")

    lines.append("# Archived but not yet consumed by an extraction script. Listed so the\n")
    lines.append("# evidence tree stays auditable: a file with no reader is a file nobody\n")
    lines.append("# has checked.\n")
    lines.append("unused_archive:\n")
    for rel, p in undescribed:
        lines.append(f'  - path: {rel}\n    bytes: {p.stat().st_size}\n    sha256: "{sha(p)}"\n')

    OUT.write_text("".join(lines))
    print(f"wrote {OUT} -- {len(described)} described, {len(undescribed)} archived")


if __name__ == "__main__":
    main()
