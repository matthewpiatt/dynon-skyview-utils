import os
import sys
import csv
import shutil
from datetime import datetime
from string import Template

METERS_PER_FOOT = 0.3048


class Config:

    def __init__(self, **kwargs):
        """
        Create a config object consisting of key/value pairs.
        Each dictionary key becomes a member of this object
        :param kwargs: key/value pairs to use as member variables
        """
        for k, v in kwargs.items():
            setattr(self, k, v)


def get_file_contents_as_string(filename: str) -> str:
    """
    Read the entire contents of a file into a string and return it with newlines unchanged
    :param filename: name of the file to read
    :return: file contents as string
    """

    with open(filename, 'r', newline='') as f:
        return f.read()


def configure_output_dir(cfg):
    """
    Configure the output directory (creating / deleting if necessary)
    :param cfg: configuration object
    :return: nothing
    """

    if cfg.delete_output_dir_on_start and os.path.exists(cfg.output_directory):
        print('Deleting previous output directory [{}]'.format(cfg.output_directory))
        shutil.rmtree(cfg.output_directory)

    print('Creating output directory [{}]'.format(cfg.output_directory))
    os.mkdir(cfg.output_directory)

    print('')


def get_session_list(cfg) -> list:
    """
    Build a list of all the sessions (identified by row numbers where the session starts),
    as detected in the user data log. A new session is started whenever the session time
    goes backwards, or the first row is read.
    :param cfg: configuration object
    :return: list of CSV rows were a new session was detected
    """
    print('Building session list...')

    session_list = list()

    csv_row_index = 0

    # read a CSV file as a dictionary
    with open(cfg.csv_input_filename, newline='') as csv_file:
        csv_reader = csv.DictReader(csv_file)

        last_session_time = -1.0

        for csv_row in csv_reader:
            # read the session times
            session_time = csv_row['Session Time']

            # continue if the session time is blank
            if not session_time:
                continue

            # convert it to a number
            session_time = float(session_time)

            # if this is the first session or the session time went backwards,
            # it must be a new session
            if (csv_row_index == 0) or (session_time < last_session_time):
                session_list.append(csv_row_index)

            last_session_time = session_time
            csv_row_index += 1

        # add a final entry at the end so consumers know when to stop
        # this final entry does not mark the start of a new session
        session_list.append(csv_row_index)

    # don't count the last, since it isn't really a session
    session_count = len(session_list) - 1
    print('Found {} sessions!'.format(session_count))

    return session_list


def open_kml_session_file(cfg, session_number):
    """
    Start a new KML session file based on the supplied session number.
    :param cfg: configuration object
    :param session_number: monotonically increasing session number suffix
    :return: (KML file handle, KML filename)
    """

    session_number = 'S' + str(session_number).zfill(3)
    kml_output_filename = cfg.kml_output_filename.format(session_number=session_number)

    print('')
    print('New session file [{}]'.format(os.path.basename(kml_output_filename)))

    # TODO inject better file metadata
    document_name = session_number
    placemark_name = session_number
    description = session_number

    kml_header_template = Template(get_file_contents_as_string(cfg.kml_template_header_filename))
    kml_header_string = kml_header_template.substitute(
        document_name=document_name,
        placemark_name=placemark_name,
        description=description,
    )

    # open the KML file for writing
    kml_file = open(kml_output_filename, 'w', newline='')

    # write the KML header
    kml_file.write(kml_header_string)

    return kml_file, kml_output_filename


def close_session_file(cfg, kml_file):
    """
    Close an existing KML session file, based on the supplied KML file handle.
    :param cfg: configuration object
    :param kml_file: KML file handle, assuming to be open
    :return: nothing
    """
    # write the KML footer
    kml_file.write(get_file_contents_as_string(cfg.kml_template_footer_filename))

    # close the file
    kml_file.close()


def delete_session_file(kml_output_filename):
    """
    Delete an existing KML session file
    :param kml_output_filename: filename to delete
    :return: nothing
    """
    os.remove(kml_output_filename)


def convert_userdatalog_csv_to_kml(cfg):
    """
    Convert a SkyView User Data Log file to a series of KML files.
    This is done in two passes.
    First, read the CSV file to determine where the session boundaries are, and build an index.
    Second, read the CSV file again and extract rows, convert to KML, and write to a KML file.
    To be memory efficient, in the second pass, the CSV file is read at the same time as a KML
    session file is being written, so as to not build up gigantic strings or lists of strings to
    write to a file later.
    :param cfg: configuration object
    :return: nothing
    """

    time_start = datetime.now()

    # note: the last index in this list is not the start of a new session
    # it only marks the end of the very last session
    session_list = get_session_list(cfg)
    if not session_list:
        print('Empty session index. Is the input file empty?')
        return

    session_index = 0
    sessions_written = 0
    sessions_detected = (len(session_list) - 1)

    empty_session_count = 0
    csv_row_index = 0
    csv_rows_rejected = 0
    kml_rows_written_this_session = 0
    kml_rows_written_total = 0

    # read a CSV file as a dictionary
    with open(cfg.csv_input_filename, newline='') as csv_file:
        csv_reader = csv.DictReader(csv_file)

        kml_file = None
        kml_output_filename = ''

        for csv_row in csv_reader:

            # we are at the start of a new session
            if session_list[session_index] == csv_row_index:
                # start a new KML session file
                kml_file, kml_output_filename = open_kml_session_file(cfg, session_index + 1)

            kml_string = generate_kml_coordinate_string(cfg, csv_row)

            if kml_string is None:
                # None indicates a bad row that should be skipped
                csv_rows_rejected += 1
            else:
                # write the data
                kml_file.write(kml_string)
                kml_rows_written_this_session += 1
                kml_rows_written_total += 1

            # the next row is a new session, so close this one
            if session_list[session_index+1] == csv_row_index+1:

                # stop the current KML session file
                close_session_file(cfg, kml_file)
                sessions_written += 1

                suffix = ''
                if kml_rows_written_this_session == 0:
                    suffix = ', (deleting)'
                    empty_session_count += 1

                    delete_session_file(kml_output_filename)
                    sessions_written -= 1

                print('Wrote {} data points{}'.format(kml_rows_written_this_session, suffix))

                session_index += 1
                kml_rows_written_this_session = 0

            csv_row_index += 1

    time_end = datetime.now()
    duration_seconds = (time_end - time_start).total_seconds()

    csv_row_rejection_pct = (float(csv_rows_rejected) / csv_row_index)
    session_rejection_pct = (1.0 - (float(sessions_written) / sessions_detected))

    print('')
    print('########################################')
    print('CSV rows read     = {}'.format(csv_row_index))
    print('CSV rows rejected = {} ({:2.2%})'.format(csv_rows_rejected, csv_row_rejection_pct))
    print('')
    print('Sessions detected = {}'.format(sessions_detected))
    print('Sessions rejected = {} ({:2.2%})'.format(sessions_written, session_rejection_pct))
    print('')
    print('KML rows written  = {}'.format(kml_rows_written_total))
    print('')
    print('Duration          = {} seconds'.format(duration_seconds))
    print('########################################')


def generate_kml_coordinate_string(cfg, csv_row) -> str:
    """
    Generate a KML coordinate string from a row of CSV data.
    :param cfg: configuration object
    :param csv_row: OrderedDict containing CSV row data
    :return: a KML coordinate string in lat,lon,alt(m) format
    """

    fix_quality = csv_row['GPS Fix Quality']
    num_sats = csv_row['Number of Satellites']
    date_and_time = csv_row['GPS Date & Time']

    # skip the row if these entries are blank
    if not fix_quality or not num_sats or not date_and_time:
        return None

    # skip the row if the quality is low
    if (int(fix_quality) < cfg.min_fix_quality) or (int(num_sats) < cfg.min_satellites):
        return None

    lat = csv_row['Latitude (deg)']
    lon = csv_row['Longitude (deg)']
    alt = csv_row['GPS Altitude (feet)']

    # skip the row if any of these fields are missing
    if not lat or not lon or not alt:
        return None

    # convert altitude from feet to meters
    alt_meters = str(float(alt) * METERS_PER_FOOT)

    kml_string = '{lon},{lat},{alt_meters}\r\n'.format(lon=lon, lat=lat, alt_meters=alt_meters)
    return kml_string


def main():

    if len(sys.argv) < 2:
        print('Extracts GPS data from a User Data Log exported from a Dynon Avionics SkyView system.')
        print('It then writes the GPS data to a series of KML files suitable for viewing in Google Earth.')
        print('')
        print('Usage: {script} [path/to/user_data_log.csv]'.format(script=os.path.basename(__file__)))
        return 1

    csv_input_filename = sys.argv[1]
    output_directory = os.path.realpath('output')

    if not os.path.exists(csv_input_filename):
        print('File [{csv_file}] does not exist!'.format(csv_file=csv_input_filename))
        return 1

    cfg = Config(
        output_directory=output_directory,
        csv_input_filename=csv_input_filename,
        kml_output_filename=os.path.join(output_directory, csv_input_filename + '_{session_number}.kml'),
        kml_template_header_filename='kml_header_template.txt',
        kml_template_footer_filename='kml_footer_template.txt',
        delete_output_dir_on_start=True,
        min_fix_quality=1,
        min_satellites=4,
    )

    configure_output_dir(cfg)
    convert_userdatalog_csv_to_kml(cfg)

    return 0


if __name__ == '__main__':

    exit_code = main()
    sys.exit(exit_code)
