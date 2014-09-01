from copy import deepcopy
from string import capwords
import datetime
from time import strptime

import guessit
from guessit.plugins.transformers import add_transformer
from flexget.utils import qualities

from .parser_common import PARSER_EPISODE, PARSER_MOVIE, PARSER_VIDEO, clean_value, old_assume_quality
from .parser_common import ParsedEntry, ParsedVideoQuality, ParsedVideo, ParsedSerie, ParsedMovie, Parser
import re

add_transformer('guess_regexp_id = flexget.utils.parsers.guess_regexp_id:GuessRegexpId')
guessit.default_options = {'name_only': True, 'clean_function': clean_value, 'allowed_languages': ['en', 'fr'], 'allowed_countries': ['us', 'uk', 'gb']}

class GuessitParsedEntry(ParsedEntry):
    def __init__(self, data, name, guess_result, **kwargs):
        ParsedEntry.__init__(self, data, name, **kwargs)
        self._guess_result = guess_result

    @property
    def parsed_group(self):
        return self._guess_result.get('releaseGroup')

    @property
    def proper_count(self):
        # todo: deprecated. We should remove this field from the rest of code.
        version = self._guess_result.get('version')
        if version is None:
            version = 0
        elif version <= 0:
            version = -1
        else:
            version = version - 1
        proper_count = self._guess_result.get('properCount', 0)
        fastsub = 'Fastsub' in self._guess_result.get('other', [])
        return version + proper_count - (5 if fastsub else 0)

    @property
    def properties(self):
        return self._guess_result


class GuessitParsedVideoQuality(ParsedVideoQuality):
    def __init__(self, guess_result):
        self._guess_result = guess_result

    @property
    def video_codec(self):
        return self._guess_result.get('videoCodec')

    @property
    def source(self):
        return self._guess_result.get('source')

    @property
    def is_screener(self):
        return 'Screener' in self._guess_result.get('other', {})

    @property
    def format(self):
        return self._guess_result.get('format')

    @property
    def audio_codec(self):
        return self._guess_result.get('audioCodec')

    @property
    def video_profile(self):
        return self._guess_result.get('videoProfile')

    @property
    def screen_size(self):
        return self._guess_result.get('screenSize')

    @property
    def audio_channels(self):
        return self._guess_result.get('audioChannels')

    @property
    def audio_profile(self):
        return self._guess_result.get('audioProfile')

    def to_old_quality(self, assumed_quality=None):
        resolution = self.screen_size if self.screen_size else 'HR' if 'HR' in self._guess_result.get('other', []) else None
        source = self.format.replace('-', '') if self.format else None
        codec = self.video_codec

        if self.audio_channels == '5.1' and self.audio_codec is None or self.audio_codec == 'DolbyDigital':
            audio = 'dd5.1'
        else:
            audio = self.audio_codec

        old_quality = qualities.Quality(' '.join(filter(None, [resolution, source, codec, audio])))
        old_quality = old_assume_quality(old_quality, assumed_quality)

        return old_quality


class GuessitParsedVideo(GuessitParsedEntry, ParsedVideo):
    def __init__(self, data, name, guess_result, **kwargs):
        GuessitParsedEntry.__init__(self, data, name, guess_result, **kwargs)
        self._quality = None

    @property
    def is_3d(self):
        return '3D' in self._guess_result.get('other', {})

    @property
    def quality2(self):
        if self._quality is None:
            self._quality = GuessitParsedVideoQuality(self._guess_result)
        return self._quality

    @property
    def subtitle_languages(self):
        return self._guess_result.get('subtitleLanguage')

    @property
    def languages(self):
        return self._guess_result.get('Language')

    @property
    def year(self):
        return self._guess_result.get('year')


class GuessitParsedMovie(GuessitParsedVideo, ParsedMovie):
    def __init__(self, data, name, guess_result, **kwargs):
        GuessitParsedVideo.__init__(self, data, name, guess_result, **kwargs)

    @property
    def title(self):
        return self._guess_result.get('title')


class GuessitParsedSerie(GuessitParsedVideo, ParsedSerie):
    part_re = re.compile('part\\s?(\\d+)', re.IGNORECASE)

    def __init__(self, data, name, guess_result, **kwargs):
        GuessitParsedVideo.__init__(self, data, name, guess_result, **kwargs)

    @property
    def series(self):
        return self._guess_result.get('series')

    @property
    def country(self):
        return str(self._guess_result.get('country')) if 'country' in self._guess_result else None

    @property
    def complete(self):
        return 'Complete' in self._guess_result.get('other', [])

    @property
    def regexp_id(self):
        regexp_id = self._guess_result.get('regexpId')
        if isinstance(regexp_id, list):
            return '-'.join(regexp_id)
        else:
            return regexp_id

    @property
    def title(self):
        return self._guess_result.get('title')

    @property
    def special(self):
        return self.episode_details and len(self.episode_details) > 0 or (self.title and self.title.lower().strip() == 'special')

    @property
    def episode_details(self):
        return self._guess_result.get('episodeDetails')

    @property
    def episode(self):
        episode = self._guess_result.get('episodeNumber')
        if episode is None and 'part' in self._guess_result:
            return self._guess_result.get('part')
        if episode is None and self.title:
            matched = self.part_re.search(self.title)
            if matched:
                return int(matched.group(1))
        return episode

    @property
    def episodes(self):
        return len(self._guess_result.get('episodeList', filter(lambda x: x is not None, [self.episode])))

    @property
    def date(self):
        d = self._guess_result.get('date')
        if d:
            if d > datetime.date.today() + datetime.timedelta(days=1):
                return None
            # Don't accept dates that are too old
            if d < datetime.date(1970, 1, 1):
                return None
            return d

    @property
    def parsed_season(self):
        season = self._guess_result.get('season')
        if season is None and self.episode and not self.allow_seasonless:
            if 'part' in self._guess_result:
                return 1
            episode_raw = self._guess_result.metadata('episodeNumber').raw
            if episode_raw and any(c.isalpha() and c.lower() != 'v' for c in episode_raw):
                return 1
        return season

    @property
    def valid_strict(self):
        return True


class GuessitParser(Parser):
    def __init__(self):
        self._type_map = {PARSER_EPISODE: 'episode', PARSER_VIDEO: 'video', PARSER_MOVIE: 'movie'}

    def build_parsed(self, guess_result, input_, type_=None, name=None, **kwargs):
        type_ = guess_result.get('type', type_)
        if (type_ == 'episode'):
            return GuessitParsedSerie(input_, name, guess_result, **kwargs)
        elif (type_ == 'movie'):
            return GuessitParsedMovie(input_, name, guess_result, **kwargs)
        elif (type_ == 'video'):
            return GuessitParsedVideo(input_, name, guess_result, **kwargs)
        else:
            return GuessitParsedEntry(input_, name, guess_result, **kwargs)

    def clean_input_name(self, name):
        name = re.sub('[_.,\[\]\(\):]', ' ', name)
        # Remove possible episode title from series name (anything after a ' - ')
        name = name.split(' - ')[0]
        # Replace some special characters with spaces
        name = re.sub('[\._\(\) ]+', ' ', name).strip(' -')
        # Normalize capitalization to title case
        name = capwords(name)
        return name

    def parse(self, data, type_=None, name=None, **kwargs):
        type_ = self._type_map.get(type_)

        guessit_options = self._guessit_options(data, type_, name, **kwargs)

        if name and name != data:
            if not guessit_options.get('strict_name'):
                guessit_options['attended_series'] = [name]

            if not type_:
                # Metainfo, we don't know if we have have a serie.
                # Grabbing serie name.
                name = self.clean_input_name(name)
                name_guessit_options = deepcopy(guessit_options)
                name_guessit_options['disabled_transformers'] = ['GuessWeakEpisodesRexps', 'GuessYear', 'GuessCountry']
                name_guess_result = guessit.guess_file_info(name, options=name_guessit_options, type=type_)
                name_guess_parsed = self.build_parsed(name_guess_result, name, type=type_, **kwargs)
                name = name_guess_parsed.name

        guess_result = guessit.guess_file_info(data, options=guessit_options, type=type_)
        return self.build_parsed(guess_result, data, type=type_, name=(name if name != data else None), **kwargs)

    def _guessit_options(self, data, type_, name, **kwargs):
        options = dict(**kwargs)
        identified_by = kwargs.get('identified_by')
        if identified_by in ['ep']:
            options['episode_prefer_number'] = False
        else:
            options['episode_prefer_number'] = True
        if kwargs.get('allow_groups'):
            options['attended_group'] = kwargs.get('allow_groups')
        return options
