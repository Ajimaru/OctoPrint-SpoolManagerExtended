# coding=utf-8


class EventBusKeys:

    EVENT_BUS_SPOOL_WEIGHT_UPDATED_AFTER_PRINT = "spool_weight_updated_after_print"
    # a scale reported a measured weight, outside of any print (see /spool/<id>/measuredWeight)
    EVENT_BUS_SPOOL_WEIGHT_MEASURED = "spool_weight_measured"
    EVENT_BUS_SPOOL_SELECTED = "spool_selected"
    EVENT_BUS_SPOOL_DESELECTED = "spool_deselected"
    EVENT_BUS_SPOOL_ADDED = "spool_added"
    EVENT_BUS_SPOOL_DELETED = "spool_deleted"
