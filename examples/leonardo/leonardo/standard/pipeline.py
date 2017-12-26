"""Data integration pipeline for project 'leonardo', standard version
"""

import datetime
from os import path

import pandas as pd
from ozelot.etl import tasks
from ozelot import config

import models


class LoadEverything(tasks.ORMWrapperTask):
    """Top-level wrapper task"""

    def requires(self):
        yield LoadPaintings()
        yield Tests()


class Tests(tasks.ORMWrapperTask):
    """Wrapper task for running all tests"""

    def requires(self):
        yield TestArtists()
        yield TestPaintings()


class ArtistsInputData(tasks.InputFileTask):
    """Input data file for artists data, with loading method"""

    input_file = path.join(config.DATA_DIR, "artists.csv")

    def load(self):
        """Load the data file, do some basic type conversions
        """

        df = pd.read_csv(self.input_file,
                         encoding='utf8')

        df['wiki_id'] = df['artist'].str.split('/').str[-1]

        # some years of birth are given as timestamps with prefix 't', convert to string
        timestamps = df['dob'].str.startswith('t')
        df.loc[timestamps, 'dob'] = df.loc[timestamps, 'dob'].str[1:].apply(
            lambda s: str(datetime.datetime.fromtimestamp(float(s))))

        df['year_of_birth'] = df['dob'].str[:4].astype(int)

        return df


class LoadArtists(tasks.ORMObjectCreatorMixin, tasks.ORMTask):
    """Load artists to DB"""

    object_classes = models.Artist

    def requires(self):
        yield ArtistsInputData()

    def run(self):
        """Load all artists into the database
        """

        df = ArtistsInputData().load()

        # rename columns
        df.rename(columns={'artistLabel': 'name',
                           'genderLabel': 'gender'},
                  inplace=True)

        # columns that exist in the data model
        columns = ['name', 'wiki_id']

        # the extended model also stores the date of birth and gender
        if config.EXTENDED:
            columns.append('gender')
            columns.append('year_of_birth')

        # keep only columns that exist in the data model
        df = df[columns]

        # append an ID column
        df['id'] = range(len(df))

        # store everything, done
        df.to_sql(name=models.Artist.__tablename__,
                  con=self.client.engine,
                  if_exists='append',
                  index=False)

        self.done()


class TestArtists(tasks.ORMTestTask):
    """Simple consistency checks for artist data"""

    def requires(self):
        yield LoadArtists()

    def run(self):
        df = self.client.df_query(self.session.query(models.Artist))

        assert len(df) == len(df.dropna(how='any')), "Found NaN values"
        assert df['wiki_id'].is_unique, "Wiki ID is not unique"

        if config.EXTENDED:
            assert df['year_of_birth'].min() > 0, "Found too small Date of Birth"
            assert df['year_of_birth'].max() < datetime.datetime.now().year, "Found too large Date of Birth"

        self.done()


class PaintingsInputData(tasks.InputFileTask):
    """Input data file for paintings, with loading method"""

    input_file = path.join(config.DATA_DIR, "paintings.csv")

    def load(self):
        """Load the data file, do some basic type conversions
        """

        df = pd.read_csv(self.input_file,
                         encoding='utf8')

        df['wiki_id'] = df['painting'].str.split('/').str[-1]
        df['creator_wiki_id'] = df['creator'].str.split('/').str[-1]
        df['decade'] = (df['inception'].str[:4].astype(float) / 10.).astype(int) * 10
        df['area'] = df['width'] * df['height']

        return df


class LoadPaintings(tasks.ORMObjectCreatorMixin, tasks.ORMTask):
    """Load paintings to DB"""

    object_classes = models.Painting

    def requires(self):
        yield LoadArtists()
        yield PaintingsInputData()

    def run(self):
        """Load all paintings into the database
        """

        df = PaintingsInputData().load()

        # rename columns
        df.rename(columns={'paintingLabel': 'name'}, inplace=True)

        # get artist IDs, map via artist wiki ID
        artist_ids = self.client.df_query(self.session.query(models.Artist.wiki_id,
                                                             models.Artist.id))
        df['artist_id'] = df['creator_wiki_id'].map(artist_ids.set_index('wiki_id')['id'])

        # limit to columns that exist in the data model
        df = df[['name', 'wiki_id', 'area', 'decade', 'artist_id']]

        # append an ID column
        df['id'] = range(len(df))

        # store everything, done
        df.to_sql(name=models.Painting.__tablename__,
                  con=self.client.engine,
                  if_exists='append',
                  index=False)

        self.done()


class TestPaintings(tasks.ORMTestTask):
    """Simple consistency checks for paintings"""

    def requires(self):
        yield LoadPaintings()

    def run(self):
        df = self.client.df_query(self.session.query(models.Painting))

        assert len(df['artist_id'].dropna()) > 0.8 * len(df), "Found too many missing artist IDs"
        assert len(df) == len(df.dropna(subset=[c for c in df.columns if not c == 'artist_id'],
                                        how='any')), "Found NaN values"
        assert df['wiki_id'].is_unique, "Wiki ID is not unique"
        assert df['area'].max() < 100., "Too large area values"
        assert df['area'].min() > 0.01 * 0.01, "Too small area values"

        self.done()
