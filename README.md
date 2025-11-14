# paper_hunter

## Google Colab

You can run paper_hunter directly in Google Colab using the following link:

[Open in Google Colab](https://colab.research.google.com/drive/14bwLx236L6G_D9cZS2_-kdoyzXIfXila?usp=sharing)

### Input CSV Requirement

The uploaded CSV file is expected to have a column named exactly `doi` (lowercase). Each row in that column should contain a valid DOI string. Example:

```
doi
10.1038/nphys1170
10.1126/science.aba2420
```

You can use `sample_doi.csv` in this repository as a template.

### Quick Usage (Colab)
1. Open the Colab link above.
2. Upload your CSV file (with the `doi` column) to the Colab environment (e.g., via the file sidebar or code cell).
3. Update any path variables in the notebook if required.
4. Run the cells to process the DOIs.

If the `doi` column is missing or differently named, the notebook will not recognize your inputs.

### Notes
- Ensure there are no extra spaces in the header (e.g., not ` doi`).
- Additional metadata columns are ignored unless otherwise documented.
- Rows with empty DOI values are skipped.