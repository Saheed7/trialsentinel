from datetime import date

import pytest
from defusedxml import EntitiesForbidden

from trialsentinel.ingestion.models import DatePrecision
from trialsentinel.ingestion.sources.pubmed import parse_pubmed_xml

XML = """<?xml version="1.0" ?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE" Owner="NLM">
      <PMID Version="1">31000001</PMID>
      <Article PubModel="Print-Electronic">
        <Journal>
          <JournalIssue><PubDate><Year>2020</Year><Month>Mar</Month></PubDate></JournalIssue>
          <Title>Lancet Respiratory Medicine</Title>
        </Journal>
        <ArticleTitle>Drug X versus placebo in <i>severe</i> asthma</ArticleTitle>
        <Abstract>
          <AbstractText Label="BACKGROUND">Asthma is common.</AbstractText>
          <AbstractText Label="RESULTS">FEV1 improved.</AbstractText>
        </Abstract>
        <DataBankList CompleteYN="Y">
          <DataBank>
            <DataBankName>ClinicalTrials.gov</DataBankName>
            <AccessionNumberList><AccessionNumber>NCT01234567</AccessionNumber></AccessionNumberList>
          </DataBank>
        </DataBankList>
        <PublicationTypeList>
          <PublicationType UI="D016449">Randomized Controlled Trial</PublicationType>
        </PublicationTypeList>
                <ArticleDate DateType="Electronic">
          <Year>2020</Year><Month>01</Month><Day>15</Day>
        </ArticleDate>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">31000001</ArticleId>
        <ArticleId IdType="doi">10.1000/xyz123</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>31000002</PMID>
      <Article>
        <Journal>
          <JournalIssue><PubDate><MedlineDate>2019 Nov-Dec</MedlineDate></PubDate></JournalIssue>
          <Title>J Test</Title>
        </Journal>
        <ArticleTitle>No abstract here</ArticleTitle>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


def test_parses_full_record() -> None:
    first, _ = parse_pubmed_xml(XML)
    assert first.pmid == "31000001"
    assert first.title == "Drug X versus placebo in severe asthma"
    assert first.abstract == "BACKGROUND: Asthma is common.\nRESULTS: FEV1 improved."
    assert first.journal == "Lancet Respiratory Medicine"
    assert first.doi == "10.1000/xyz123"
    assert first.registry_ids == ["NCT01234567"]
    assert first.publication_types == ["Randomized Controlled Trial"]
    assert first.pub_date is not None
    assert first.pub_date.earliest == date(2020, 1, 15)  # electronic date preferred
    assert first.pub_date.precision == DatePrecision.DAY


def test_handles_sparse_record() -> None:
    _, second = parse_pubmed_xml(XML)
    assert second.abstract is None
    assert second.registry_ids == []
    assert second.pub_date is not None
    assert second.pub_date.precision == DatePrecision.YEAR
    assert second.pub_date.earliest == date(2019, 1, 1)


def test_rejects_entity_expansion_payload() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY boom "aaaaaaaa">]>'
        "<PubmedArticleSet>&boom;</PubmedArticleSet>"
    )
    with pytest.raises(EntitiesForbidden):
        parse_pubmed_xml(evil)
